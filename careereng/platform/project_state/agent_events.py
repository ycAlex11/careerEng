"""Durable main-agent events for workspace-scoped execution."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import RLock
from time import monotonic, sleep
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
        self.main_agent_path = self.root / "main_agent.json"
        self.dispatcher = dispatcher or AgentEventDispatcher()
        self._lock = RLock()

    def register_main_agent(
        self,
        *,
        thread_id: str,
        consumer_id: str = "codex_desktop",
        allow_takeover: bool = False,
    ) -> dict[str, Any]:
        """Persist the one App Server thread allowed to receive main-agent events."""

        normalized_thread_id = str(thread_id or "").strip()
        if not normalized_thread_id:
            raise ValueError("main-agent thread_id is required")
        existing = self.main_agent_registration()
        existing_thread = str(existing.get("thread_id") or "")
        if existing_thread and existing_thread != normalized_thread_id and not allow_takeover:
            raise ValueError(f"main agent already registered: {existing_thread}")
        latest_sequence = self._latest_sequence()
        delivery_after_sequence = (
            int(existing.get("delivery_after_sequence") or 0)
            if "delivery_after_sequence" in existing
            else latest_sequence
        )
        registration = {
            "thread_id": normalized_thread_id,
            "consumer_id": str(consumer_id or "codex_desktop").strip() or "codex_desktop",
            "registered_at": str(existing.get("registered_at") or now_iso()),
            "validated_at": now_iso(),
            "delivery_after_sequence": delivery_after_sequence,
        }
        with self._lock:
            write_json(self.main_agent_path, registration)
        return registration

    def main_agent_registration(self) -> dict[str, Any]:
        """Return the current workspace-scoped main-agent target, if registered."""

        with self._lock:
            payload = read_json(self.main_agent_path)
        if not isinstance(payload, dict):
            return {}
        thread_id = str(payload.get("thread_id") or "").strip()
        if not thread_id:
            return {}
        return {
            "thread_id": thread_id,
            "consumer_id": str(payload.get("consumer_id") or "codex_desktop").strip() or "codex_desktop",
            "registered_at": str(payload.get("registered_at") or ""),
            "validated_at": str(payload.get("validated_at") or ""),
            "delivery_after_sequence": int(payload.get("delivery_after_sequence") or 0),
        }

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
                for existing in self.events.iter_rows_reverse():
                    if str(existing.get("dedupe_key") or "") == normalized_dedupe_key:
                        return existing
            event["sequence"] = self._latest_sequence() + 1
            self.events.append(event)
        self.dispatcher.dispatch(event)
        return event

    def _latest_sequence(self) -> int:
        for row in self.events.iter_rows_reverse():
            try:
                return int(row.get("sequence") or 0)
            except (TypeError, ValueError):
                return 0
        return 0

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
            stored_cursor = str(cursors.get(consumer) or "")
            effective_cursor = requested_cursor or stored_cursor
            registration = self.main_agent_registration()
        minimum_sequence = 0
        if not effective_cursor and str(registration.get("consumer_id") or "") == consumer:
            minimum_sequence = int(registration.get("delivery_after_sequence") or 0)
        filtered: list[dict[str, Any]] = []
        normalized_site = str(site_key or "").strip()
        cursor_can_advance = not normalized_site and include_notifications
        scanned_cursor = effective_cursor
        for row in self._iter_rows_after_cursor(effective_cursor):
            if int(row.get("sequence") or 0) <= minimum_sequence:
                continue
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
            if not any(
                str(row.get("event_id") or "") == acknowledged_cursor
                for row in self.events.iter_rows_reverse()
            ):
                raise ValueError(f"unknown agent event cursor: {acknowledged_cursor}")
        with self._lock:
            cursors = self._load_cursors()
            cursors[consumer] = acknowledged_cursor
            write_json(self.cursors_path, {"consumers": cursors, "updated_at": now_iso()})
        return {
            "consumer_id": consumer,
            "cursor": acknowledged_cursor,
            "acknowledged_sequence": self.acknowledged_sequence(consumer_id=consumer),
        }

    def wait_events(
        self,
        *,
        consumer_id: str = "codex_desktop",
        cursor: str = "",
        site_key: str = "",
        include_notifications: bool = True,
        limit: int = 100,
        timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 0.25,
    ) -> dict[str, Any]:
        """Long-poll the durable inbox without relying on a callback write."""

        timeout = min(60.0, max(0.0, float(timeout_seconds or 0.0)))
        poll_interval = min(1.0, max(0.05, float(poll_interval_seconds or 0.25)))
        deadline = monotonic() + timeout
        while True:
            result = self.list_events(
                consumer_id=consumer_id,
                cursor=cursor,
                site_key=site_key,
                include_notifications=include_notifications,
                limit=limit,
            )
            if result["events"]:
                return {**result, "timed_out": False}
            remaining = deadline - monotonic()
            if remaining <= 0:
                return {**result, "timed_out": True}
            sleep(min(poll_interval, remaining))

    def acknowledged_sequence(self, *, consumer_id: str = "codex_desktop") -> int:
        """Return the monotonic sequence consumed by one inbox reader."""

        consumer = str(consumer_id or "codex_desktop").strip() or "codex_desktop"
        with self._lock:
            cursor = str(self._load_cursors().get(consumer) or "")
        if not cursor:
            return 0
        for row in self.events.iter_rows_reverse():
            if str(row.get("event_id") or "") != cursor:
                continue
            try:
                return int(row.get("sequence") or 0)
            except (TypeError, ValueError):
                return 0
        return 0

    def is_acknowledged(self, event: dict[str, Any], *, consumer_id: str = "codex_desktop") -> bool:
        """Report whether a durable event is behind the consumer cursor."""

        try:
            sequence = int(event.get("sequence") or 0)
        except (TypeError, ValueError):
            sequence = 0
        return sequence > 0 and sequence <= self.acknowledged_sequence(consumer_id=consumer_id)

    def _load_cursors(self) -> dict[str, str]:
        payload = read_json(self.cursors_path)
        rows = payload.get("consumers") if isinstance(payload.get("consumers"), dict) else {}
        return {str(key): str(value) for key, value in rows.items() if str(key)}

    def _iter_rows_after_cursor(self, cursor: str):
        """Stream rows after a durable cursor without materializing the inbox."""

        if not cursor:
            yield from self.events.iter_rows()
            return
        found_cursor = False
        for row in self.events.iter_rows():
            if str(row.get("event_id") or "") == cursor:
                found_cursor = True
                continue
            if found_cursor:
                yield row
        if not found_cursor:
            # Preserve the former compatibility behavior for an unknown cursor.
            yield from self.events.iter_rows()
