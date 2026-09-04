"""Deliver durable CareerEng attention events to one registered Codex main thread.

The event store remains authoritative. This adapter only provides best-effort
local App Server delivery after an event has been persisted, so Codex-specific
transport failures never lose a user-required event.
"""

from __future__ import annotations

from pathlib import Path
from threading import Event, Lock, Thread
from time import time
from typing import Any, Callable

from careereng.adapters.codex.app_server import CodexAppServerClient
from careereng.platform.project_state import AgentEventStore
from careereng.utils import now_iso, read_json, write_json


_ATTENTION_EVENTS = frozenset({"action_required", "review_required"})
_PROGRESS_EVENTS = frozenset(
    {
        "site.phase_advanced",
        "site.completed",
        "batch.completed",
        "evolution.requested",
        "evolution.resolved",
        "evolution.failed",
    }
)
_DELIVERY_PENDING = "pending"
_DELIVERY_DEFERRED_ACTIVE = "deferred_active"
_DELIVERY_DELIVERED = "delivered"


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
        retry_interval_seconds: float = 1.0,
        retry_max_delay_seconds: float = 60.0,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.event_store = event_store
        self.app_server_factory = app_server_factory or (
            lambda: CodexAppServerClient(cwd=self.project_root)
        )
        self.delivery_path = self.event_store.root / "main_agent_deliveries.json"
        self._lock = Lock()
        self._server: CodexAppServerClient | None = None
        self._retry_interval_seconds = max(0.05, float(retry_interval_seconds or 1.0))
        self._retry_max_delay_seconds = max(
            self._retry_interval_seconds,
            float(retry_max_delay_seconds or 60.0),
        )
        self._stop_retry = Event()
        self._retry_thread: Thread | None = None
        self._attached = False

    def attach(self) -> None:
        """Subscribe once to future durable events in this runtime host."""

        if self._attached:
            return
        self._attached = True
        self.event_store.dispatcher.register(self.enqueue)
        self._retry_thread = Thread(
            target=self._retry_loop,
            name="careereng-main-agent-delivery",
            daemon=True,
        )
        self._retry_thread.start()

    def close(self) -> None:
        self._stop_retry.set()
        retry_thread = self._retry_thread
        if retry_thread is not None and retry_thread.is_alive():
            retry_thread.join(timeout=max(1.0, self._retry_interval_seconds * 2))
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
        if not event_id or self._delivery_status(event_id) == _DELIVERY_DELIVERED:
            return
        Thread(target=self.deliver_event, args=(event,), name=f"careereng-main-agent-{event_id}", daemon=True).start()

    def deliver_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Deliver one event once; failed delivery remains durable and retryable."""

        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            return {"delivered": False, "error": "event_id is required"}
        with self._lock:
            if self.event_store.is_acknowledged(event):
                return {"delivered": True, "acknowledged": True}
            if self._delivery_status(event_id) == _DELIVERY_DELIVERED:
                return {"delivered": True, "duplicate": True}
            registration = self.event_store.main_agent_registration()
            thread_id = str(registration.get("thread_id") or "").strip()
            if not thread_id:
                self._record_delivery(
                    event_id,
                    status=_DELIVERY_PENDING,
                    error="main_agent_not_registered",
                )
                return {"delivered": False, "error": "main_agent_not_registered"}
            try:
                server = self._ensure_server()
                server.resume_thread(thread_id)
                server.start_turn(thread_id=thread_id, prompt=_main_agent_prompt(event))
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                status = _DELIVERY_DEFERRED_ACTIVE if _is_active_writer_error(exc) else _DELIVERY_PENDING
                self._record_delivery(event_id, status=status, error=error)
                return {
                    "delivered": False,
                    "deferred": status == _DELIVERY_DEFERRED_ACTIVE,
                    "error": str(exc),
                }
            self._record_delivery(event_id, status=_DELIVERY_DELIVERED, error="")
            return {"delivered": True, "thread_id": thread_id}

    def retry_pending(self, *, force: bool = False) -> int:
        """Retry deliverable events after a bridge or App Server restart."""

        delivered = 0
        registration = self.event_store.main_agent_registration()
        delivery_after = int(registration.get("delivery_after_sequence") or 0)
        events = [
            event
            for event in self.event_store.events.iter_rows()
            if isinstance(event, dict) and _is_deliverable(event)
        ]
        events.sort(key=lambda event: (_delivery_priority(event), int(event.get("sequence") or 0)))
        for event in events:
            if int(event.get("sequence") or 0) <= delivery_after:
                continue
            if self.event_store.is_acknowledged(event):
                continue
            event_id = str(event.get("event_id") or "")
            if self._delivery_status(event_id) == _DELIVERY_DELIVERED:
                continue
            if not force and not self._retry_due(event_id):
                continue
            if self.deliver_event(event).get("delivered"):
                delivered += 1
        return delivered

    def _retry_loop(self) -> None:
        while not self._stop_retry.wait(self._retry_interval_seconds):
            try:
                self.retry_pending()
            except Exception:
                continue

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

    def _retry_due(self, event_id: str) -> bool:
        payload = self._load_deliveries()
        deliveries = payload.get("deliveries") if isinstance(payload.get("deliveries"), dict) else {}
        row = deliveries.get(str(event_id))
        if not isinstance(row, dict):
            return True
        try:
            retry_after = float(row.get("retry_after_epoch") or 0.0)
        except (TypeError, ValueError):
            retry_after = 0.0
        return retry_after <= time()

    def _record_delivery(self, event_id: str, *, status: str, error: str) -> None:
        payload = self._load_deliveries()
        rows = payload.setdefault("deliveries", {})
        previous = rows.get(event_id) if isinstance(rows.get(event_id), dict) else {}
        attempts = int(previous.get("attempts") or 0) + 1
        retry_after_epoch = 0.0
        if status in {_DELIVERY_PENDING, _DELIVERY_DEFERRED_ACTIVE}:
            delay = min(
                self._retry_max_delay_seconds,
                self._retry_interval_seconds * (2 ** min(max(0, attempts - 1), 6)),
            )
            retry_after_epoch = time() + delay
        rows[event_id] = {
            "status": status,
            "attempts": attempts,
            "last_error": str(error or ""),
            "retry_after_epoch": retry_after_epoch,
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


def _is_active_writer_error(error: Exception) -> bool:
    message = str(error or "").strip().lower()
    return "active writer" in message or "already has an active turn" in message


def _delivery_priority(event: dict[str, Any]) -> int:
    attention = str(event.get("attention") or "")
    kind = str(event.get("kind") or "")
    if attention in _ATTENTION_EVENTS:
        return 0
    if kind in {"site.completed", "batch.completed", "evolution.requested", "evolution.failed"}:
        return 1
    return 2


def _main_agent_prompt(event: dict[str, Any]) -> str:
    """Keep the wakeup generic; main-agent reasoning stays in Codex/Skills."""

    site_key = str(event.get("site_key") or "").strip()
    kind = str(event.get("kind") or "attention_required").strip()
    if kind == "evolution.requested":
        return (
            "CareerEng created a durable non-blocking evolution work item. "
            "Call careereng_list_agent_events, inspect the referenced action card and evolution run, "
            "then use the existing proposal/apply/evaluation flow without asking for routine approval. "
            "Do not restart the completed business batch. "
            f"Site: {site_key or 'workspace'}."
        )
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


def main_agent_delivery_health(event_store: AgentEventStore) -> dict[str, Any]:
    """Project pending delivery state without retrying or mutating it."""

    registration = event_store.main_agent_registration()
    payload = read_json(event_store.root / "main_agent_deliveries.json")
    deliveries = payload.get("deliveries") if isinstance(payload.get("deliveries"), dict) else {}
    delivery_after = int(registration.get("delivery_after_sequence") or 0)
    acknowledged_sequence = event_store.acknowledged_sequence(consumer_id="codex_desktop")
    event_by_id = {
        str(row.get("event_id") or ""): row
        for row in event_store.events.iter_rows()
        if isinstance(row, dict)
    }
    pending = []
    for event_id, event in event_by_id.items():
        sequence = int(event.get("sequence") or 0)
        if sequence <= delivery_after or sequence <= acknowledged_sequence or not _is_deliverable(event):
            continue
        row = deliveries.get(event_id) if isinstance(deliveries.get(event_id), dict) else {}
        delivery_status = str(row.get("status") or "queued")
        if delivery_status == _DELIVERY_DELIVERED:
            continue
        pending.append(
            {
                "event_id": str(event_id),
                "sequence": sequence,
                "kind": str(event.get("kind") or ""),
                "status": delivery_status,
                "last_error": str(row.get("last_error") or ""),
                "attempts": int(row.get("attempts") or 0),
                "updated_at": str(row.get("updated_at") or ""),
            }
        )
    pending.sort(key=lambda row: int(row["sequence"]))
    deferred_count = sum(1 for row in pending if row["status"] == _DELIVERY_DEFERRED_ACTIVE)
    health_status = "healthy"
    if pending:
        health_status = "deferred" if deferred_count == len(pending) else "degraded"
    return {
        "registered": bool(registration.get("thread_id")),
        "thread_id": str(registration.get("thread_id") or ""),
        "acknowledged_sequence": acknowledged_sequence,
        "pending_count": len(pending),
        "deferred_active_count": deferred_count,
        "oldest_pending": pending[0] if pending else {},
        "status": health_status if registration.get("thread_id") else "degraded",
    }
