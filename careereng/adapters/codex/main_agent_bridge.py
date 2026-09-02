"""Deliver durable CareerEng attention events to one registered Codex main thread.

The event store remains authoritative. This adapter only provides best-effort
local App Server delivery after an event has been persisted, so Codex-specific
transport failures never lose a user-required event.
"""

from __future__ import annotations

from pathlib import Path
from threading import Lock, Thread
from typing import Any, Callable

from careereng.adapters.codex.app_server import CodexAppServerClient
from careereng.platform.project_state import AgentEventStore
from careereng.utils import now_iso, read_json, write_json


_ATTENTION_EVENTS = frozenset({"action_required", "review_required"})
_PROGRESS_EVENTS = frozenset({"site.phase_advanced", "site.completed", "batch.completed"})


def _is_deliverable(event: dict[str, Any]) -> bool:
    return (
        str(event.get("attention") or "") in _ATTENTION_EVENTS
        or str(event.get("kind") or "") in _PROGRESS_EVENTS
    )


class CodexMainAgentBridge:
    """Asynchronously wake the registered main thread for durable key events."""

    def __init__(
        self,
        *,
        project_root: Path,
        event_store: AgentEventStore,
        app_server_factory: Callable[[], CodexAppServerClient] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.event_store = event_store
        self.app_server_factory = app_server_factory or (
            lambda: CodexAppServerClient(cwd=self.project_root)
        )
        self.delivery_path = self.event_store.root / "main_agent_deliveries.json"
        self._lock = Lock()
        self._server: CodexAppServerClient | None = None

    def attach(self) -> None:
        """Subscribe once to future durable events in this runtime host."""

        self.event_store.dispatcher.register(self.enqueue)

    def close(self) -> None:
        with self._lock:
            server = self._server
            self._server = None
        if server is not None:
            server.close()

    def enqueue(self, event: dict[str, Any]) -> None:
        """Schedule delivery without blocking the workflow that wrote the event."""

        if not _is_deliverable(event):
            return
        event_id = str(event.get("event_id") or "").strip()
        if not event_id or self._delivery_status(event_id) == "delivered":
            return
        Thread(target=self.deliver_event, args=(event,), name=f"careereng-main-agent-{event_id}", daemon=True).start()

    def deliver_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Deliver one event once; failed delivery remains durable and retryable."""

        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            return {"delivered": False, "error": "event_id is required"}
        with self._lock:
            if self._delivery_status(event_id) == "delivered":
                return {"delivered": True, "duplicate": True}
            registration = self.event_store.main_agent_registration()
            thread_id = str(registration.get("thread_id") or "").strip()
            if not thread_id:
                self._record_delivery(event_id, status="pending", error="main_agent_not_registered")
                return {"delivered": False, "error": "main_agent_not_registered"}
            try:
                server = self._ensure_server()
                server.resume_thread(thread_id)
                server.start_turn(thread_id=thread_id, prompt=_main_agent_prompt(event))
            except Exception as exc:
                self._record_delivery(event_id, status="pending", error=f"{type(exc).__name__}: {exc}")
                return {"delivered": False, "error": str(exc)}
            self._record_delivery(event_id, status="delivered", error="")
            return {"delivered": True, "thread_id": thread_id}

    def retry_pending(self) -> int:
        """Retry deliverable events after a bridge or App Server restart."""

        delivered = 0
        for event in self.event_store.events.iter_rows():
            if not isinstance(event, dict) or not _is_deliverable(event):
                continue
            if self._delivery_status(str(event.get("event_id") or "")) == "delivered":
                continue
            if self.deliver_event(event).get("delivered"):
                delivered += 1
        return delivered

    def _ensure_server(self) -> CodexAppServerClient:
        if self._server is None:
            self._server = self.app_server_factory()
        self._server.start()
        return self._server

    def _delivery_status(self, event_id: str) -> str:
        payload = self._load_deliveries()
        deliveries = payload.get("deliveries") if isinstance(payload.get("deliveries"), dict) else {}
        row = deliveries.get(str(event_id))
        return str(row.get("status") or "") if isinstance(row, dict) else ""

    def _record_delivery(self, event_id: str, *, status: str, error: str) -> None:
        payload = self._load_deliveries()
        rows = payload.setdefault("deliveries", {})
        previous = rows.get(event_id) if isinstance(rows.get(event_id), dict) else {}
        rows[event_id] = {
            "status": status,
            "attempts": int(previous.get("attempts") or 0) + 1,
            "last_error": str(error or ""),
            "updated_at": now_iso(),
        }
        write_json(self.delivery_path, payload)

    def _load_deliveries(self) -> dict[str, Any]:
        payload = read_json(self.delivery_path)
        if not isinstance(payload, dict):
            return {"deliveries": {}}
        if not isinstance(payload.get("deliveries"), dict):
            payload["deliveries"] = {}
        return payload


def _main_agent_prompt(event: dict[str, Any]) -> str:
    """Keep the wakeup generic; main-agent reasoning stays in Codex/Skills."""

    site_key = str(event.get("site_key") or "").strip()
    kind = str(event.get("kind") or "attention_required").strip()
    if str(event.get("attention") or "") == "notification":
        return (
            "CareerEng has a durable execution progress event. "
            "Call careereng_list_agent_events, report the new phase or completion milestone concisely, "
            "and acknowledge only the events you handled. "
            f"Event kind: {kind}. Site: {site_key or 'workspace'}."
        )
    return (
        "CareerEng has a durable event requiring your attention. "
        "Call careereng_list_agent_events before taking action. "
        f"Event kind: {kind}. Site: {site_key or 'workspace'}. "
        "Report the required user action concisely, then acknowledge only the events you handled."
    )
