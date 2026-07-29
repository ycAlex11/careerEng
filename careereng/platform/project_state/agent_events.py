"""Durable main-agent events for workspace-scoped execution."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import RLock
from typing import Any

from careereng.platform.persistence import JSONLStore
from careereng.utils import ensure_dir, make_id, now_iso, read_json, write_json


ATTENTION_ACTION_REQUIRED = "action_required"
ATTENTION_REVIEW_REQUIRED = "review_required"
ATTENTION_NOTIFICATION = "notification"
ATTENTION_AUDIT = "audit"
ATTENTION_VALUES = frozenset(
    {
        ATTENTION_ACTION_REQUIRED,
        ATTENTION_REVIEW_REQUIRED,
        ATTENTION_NOTIFICATION,
        ATTENTION_AUDIT,
    }
)


class AgentEventDispatcher:
    """Best-effort extension point for future main-agent wakeups.

    Events are always stored before listeners run. A listener therefore cannot
    make a user-required event disappear if a desktop receiver is unavailable.
    """

    def __init__(self) -> None:
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._lock = RLock()

    def register(self, listener: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def dispatch(self, event: dict[str, Any]) -> None:
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(dict(event))
            except Exception:
                # Delivery is optional. The durable inbox remains authoritative.
                continue


class AgentEventStore:
    """Append-only main-agent inbox with consumer-local acknowledgement cursors."""

    def __init__(self, workspace: Path | str, *, dispatcher: AgentEventDispatcher | None = None):
        self.workspace = Path(workspace)
        self.root = ensure_dir(self.workspace / "agent_events")
        self.events = JSONLStore(self.root / "events.jsonl")
        self.cursors_path = self.root / "cursors.json"
        self.dispatcher = dispatcher or AgentEventDispatcher()
        self._lock = RLock()

    def publish(
        self,
        *,
        kind: str,
        attention: str,
        summary: str,
        site_key: str = "",
        batch_id: str = "",
        thread_id: str = "",
        turn_id: str = "",
        phase: str = "",
        current_url: str = "",
        details: dict[str, Any] | None = None,
        dedupe_key: str = "",
    ) -> dict[str, Any]:
        normalized_attention = str(attention or ATTENTION_NOTIFICATION).strip().lower()
        if normalized_attention not in ATTENTION_VALUES:
            raise ValueError(f"unsupported agent event attention: {normalized_attention}")
        normalized_dedupe_key = str(dedupe_key or "").strip()
        event = {
            "event_id": make_id("agent_event"),
            "created_at": now_iso(),
            "kind": str(kind or "agent_event").strip() or "agent_event",
            "attention": normalized_attention,
            "summary": str(summary or "").strip(),
            "site_key": str(site_key or "").strip(),
            "batch_id": str(batch_id or "").strip(),
            "thread_id": str(thread_id or "").strip(),
            "turn_id": str(turn_id or "").strip(),
            "phase": str(phase or "").strip(),
            "current_url": str(current_url or "").strip(),
            "details": dict(details or {}),
            "dedupe_key": normalized_dedupe_key,
        }
        with self._lock:
            if normalized_dedupe_key:
                for existing in self.events.read_all():
                    if str(existing.get("dedupe_key") or "") == normalized_dedupe_key:
                        return existing
            self.events.append(event)
        self.dispatcher.dispatch(event)
        return event

    def list_events(
        self,
        *,
        consumer_id: str = "codex_desktop",
        cursor: str = "",
        site_key: str = "",
        include_notifications: bool = True,
        limit: int = 100,
    ) -> dict[str, Any]:
        consumer = str(consumer_id or "codex_desktop").strip() or "codex_desktop"
        requested_cursor = str(cursor or "").strip()
        with self._lock:
            cursors = self._load_cursors()
            effective_cursor = requested_cursor or str(cursors.get(consumer) or "")
            rows = self.events.read_all()
        start_index = self._index_after_cursor(rows, effective_cursor)
        filtered: list[dict[str, Any]] = []
        normalized_site = str(site_key or "").strip()
        cursor_can_advance = not normalized_site and include_notifications
        scanned_cursor = effective_cursor
        for row in rows[start_index:]:
            scanned_cursor = str(row.get("event_id") or scanned_cursor)
            if normalized_site and str(row.get("site_key") or "") != normalized_site:
                continue
            if not include_notifications and str(row.get("attention") or "") == ATTENTION_NOTIFICATION:
                continue
            if str(row.get("attention") or "") == ATTENTION_AUDIT:
                continue
            filtered.append(dict(row))
            if len(filtered) >= max(1, int(limit or 100)):
                break
        next_cursor = scanned_cursor if cursor_can_advance else effective_cursor
        return {
            "consumer_id": consumer,
            "cursor": effective_cursor,
            "next_cursor": next_cursor,
            "events": filtered,
            "has_attention_required": any(
                str(row.get("attention") or "") in {ATTENTION_ACTION_REQUIRED, ATTENTION_REVIEW_REQUIRED}
                for row in filtered
            ),
        }

    def acknowledge(self, *, consumer_id: str, cursor: str) -> dict[str, Any]:
        consumer = str(consumer_id or "codex_desktop").strip() or "codex_desktop"
        acknowledged_cursor = str(cursor or "").strip()
        if acknowledged_cursor:
            event_ids = {str(row.get("event_id") or "") for row in self.events.read_all()}
            if acknowledged_cursor not in event_ids:
                raise ValueError(f"unknown agent event cursor: {acknowledged_cursor}")
        with self._lock:
            cursors = self._load_cursors()
            cursors[consumer] = acknowledged_cursor
            write_json(self.cursors_path, {"consumers": cursors, "updated_at": now_iso()})
        return {"consumer_id": consumer, "cursor": acknowledged_cursor}

    def _load_cursors(self) -> dict[str, str]:
        payload = read_json(self.cursors_path)
        rows = payload.get("consumers") if isinstance(payload.get("consumers"), dict) else {}
        return {str(key): str(value) for key, value in rows.items() if str(key)}

    @staticmethod
    def _index_after_cursor(rows: list[dict[str, Any]], cursor: str) -> int:
        if not cursor:
            return 0
        for index, row in enumerate(rows):
            if str(row.get("event_id") or "") == cursor:
                return index + 1
        return 0
