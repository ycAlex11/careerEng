"""Durable index for externally executable CareerEng work items.

Browser session metadata is useful for display, but it is mutable runtime
state.  MCP authorization resolves work items from this index instead, so a
browser restart or session update cannot silently orphan an active task.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from careereng.utils import ensure_dir, now_iso, read_json, write_json

from .work_items import work_item_id_from_payload
from careereng.orchestration.worker_control import WorkItemFence, can_transition, next_control_epoch, validate_work_item_fence


class WorkItemStore:
    """Persist and resolve immutable work-item scope plus lifecycle evidence."""

    def __init__(self, workspace: Path | str) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.root = ensure_dir(self.workspace / "agent_bridge" / "work_items")
        self.index_path = self.root / "index.json"
        self.events_path = self.root / "events.jsonl"
        self._lock = threading.RLock()

    def register(self, payload_path: Path | str, *, event: str = "created") -> dict[str, Any]:
        """Create or refresh an index entry from the persisted work item."""

        payload, path = self._load_payload(payload_path)
        work_item_id = work_item_id_from_payload(payload)
        if not work_item_id:
            raise ValueError("work item has no stable identifier")
        site_key = str(payload.get("site_key") or "").strip()
        batch_id = str(payload.get("batch_id") or "").strip()
        if not site_key or not batch_id:
            raise ValueError("work item has incomplete execution scope")
        with self._lock:
            index = read_json(self.index_path)
            records = index.get("records") if isinstance(index.get("records"), dict) else {}
            previous = records.get(work_item_id) if isinstance(records.get(work_item_id), dict) else {}
            requested_state = str(payload.get("worker_state") or previous.get("state") or "active").strip()
            previous_state = str(previous.get("state") or "").strip()
            state_accepted = not previous_state or can_transition(previous_state, requested_state)
            state = requested_state if state_accepted else previous_state
            payload_context_revision = max(0, int(payload.get("context_revision") or 0))
            previous_context_revision = max(0, int(previous.get("context_revision") or 0))
            context_revision = payload_context_revision if state_accepted else previous_context_revision
            site_revision = max(1, int(previous.get("site_revision") or 1))
            if previous and (
                state != previous_state
                or context_revision != previous_context_revision
            ):
                site_revision += 1
            record = {
                **previous,
                "work_item_id": work_item_id,
                "site_key": site_key,
                "batch_id": batch_id,
                "session_id": str(payload.get("session_id") or ""),
                "turn_id": str(payload.get("turn_id") or ""),
                "payload_path": str(path),
                "state": state,
                "context_revision": context_revision,
                "control_epoch": max(1, int(previous.get("control_epoch") or 1)),
                "site_revision": site_revision,
                "updated_at": now_iso(),
            }
            record.setdefault("created_at", record["updated_at"])
            records[work_item_id] = record
            write_json(self.index_path, {"updated_at": now_iso(), "records": records})
            self._append_event(event, record)
            return record

    def transition(self, work_item_id: str, *, state: str, event: str) -> dict[str, Any]:
        """Record a lifecycle transition without changing the work-item scope."""

        with self._lock:
            index = read_json(self.index_path)
            records = index.get("records") if isinstance(index.get("records"), dict) else {}
            record = records.get(str(work_item_id or ""))
            if not isinstance(record, dict):
                raise ValueError("work item was not found in durable index")
            if not can_transition(record.get("state"), state):
                raise ValueError(f"stale work item transition: {record.get('state')} -> {state}")
            updated = {
                **record,
                "state": str(state or "").strip(),
                "site_revision": int(record.get("site_revision") or 0) + 1,
                "updated_at": now_iso(),
            }
            records[str(work_item_id)] = updated
            write_json(self.index_path, {"updated_at": now_iso(), "records": records})
            self._append_event(event, updated)
            return updated

    def compare_and_transition(
        self,
        work_item_id: str,
        *,
        expected_revision: int,
        state: str,
        event: str,
    ) -> dict[str, Any]:
        """Apply a lifecycle event only to the revision it observed."""

        with self._lock:
            index = read_json(self.index_path)
            records = index.get("records") if isinstance(index.get("records"), dict) else {}
            record = records.get(str(work_item_id or ""))
            if not isinstance(record, dict):
                raise ValueError("work item was not found in durable index")
            current_revision = int(record.get("site_revision") or 0)
            if current_revision != int(expected_revision):
                raise ValueError("work item state revision is stale")
            if not can_transition(record.get("state"), state):
                raise ValueError(f"stale work item transition: {record.get('state')} -> {state}")
            updated = {
                **record,
                "state": str(state or "").strip(),
                "site_revision": current_revision + 1,
                "updated_at": now_iso(),
            }
            records[str(work_item_id)] = updated
            write_json(self.index_path, {"updated_at": now_iso(), "records": records})
            self._append_event(event, updated)
            return updated

    def revoke_scope(self, *, site_key: str, batch_id: str = "", state: str = "pausing", event: str = "revoked") -> list[dict[str, Any]]:
        """Atomically revoke executable leases for one site scope."""

        with self._lock:
            index = read_json(self.index_path)
            records = index.get("records") if isinstance(index.get("records"), dict) else {}
            changed: list[dict[str, Any]] = []
            for work_item_id, record in list(records.items()):
                if not isinstance(record, dict) or str(record.get("site_key") or "") != str(site_key or ""):
                    continue
                if batch_id and str(record.get("batch_id") or "") != str(batch_id):
                    continue
                if str(record.get("state") or "") in {"completed", "released", "cancelled"}:
                    continue
                updated = {
                    **record,
                    "state": str(state or "pausing"),
                    "control_epoch": next_control_epoch(record.get("control_epoch")),
                    "site_revision": int(record.get("site_revision") or 0) + 1,
                    "updated_at": now_iso(),
                }
                records[work_item_id] = updated
                self._append_event(event, updated)
                changed.append(updated)
            if changed:
                write_json(self.index_path, {"updated_at": now_iso(), "records": records})
            return changed

    def reissue(self, work_item_id: str, *, event: str = "reissued") -> dict[str, Any]:
        """Issue a fresh executable lease for one retained work item."""

        with self._lock:
            index = read_json(self.index_path)
            records = index.get("records") if isinstance(index.get("records"), dict) else {}
            record = records.get(str(work_item_id or ""))
            if not isinstance(record, dict):
                raise ValueError("work item was not found in durable index")
            if str(record.get("state") or "") in {"completed", "cancelled", "released"}:
                raise ValueError("terminal work item cannot be reissued")
            updated = {
                **record,
                "state": "active",
                "control_epoch": next_control_epoch(record.get("control_epoch")),
                "site_revision": int(record.get("site_revision") or 0) + 1,
                "updated_at": now_iso(),
            }
            records[str(work_item_id)] = updated
            write_json(self.index_path, {"updated_at": now_iso(), "records": records})
            self._append_event(event, updated)
            return updated

    def validate_fence(self, fence: WorkItemFence, *, require_revision: bool = True) -> dict[str, Any]:
        """Validate one caller lease against the durable control record."""

        with self._lock:
            index = read_json(self.index_path)
            records = index.get("records") if isinstance(index.get("records"), dict) else {}
            record = records.get(fence.work_item_id)
            if not isinstance(record, dict):
                raise ValueError("work item was not found in durable index")
            validate_work_item_fence(record, fence, require_revision=require_revision)
            return dict(record)

    def resolve_active(self, work_item_id: str) -> dict[str, Any]:
        """Load a live work item after verifying its durable indexed scope."""

        requested = str(work_item_id or "").strip()
        index = read_json(self.index_path)
        records = index.get("records") if isinstance(index.get("records"), dict) else {}
        record = records.get(requested)
        if not isinstance(record, dict):
            record = self._discover(requested)
        if not isinstance(record, dict):
            raise ValueError("active work item was not found")
        if str(record.get("state") or "") != "active":
            raise ValueError(f"work item is not executable: {record.get('state') or 'unknown'}")
        payload, _ = self._load_payload(record.get("payload_path"))
        if work_item_id_from_payload(payload) != requested:
            raise ValueError("work item payload does not match durable index")
        for field in ("site_key", "batch_id"):
            if str(payload.get(field) or "") != str(record.get(field) or ""):
                raise ValueError("work item payload scope does not match durable index")
        return {
            **payload,
            "control_epoch": int(record.get("control_epoch") or 0),
            "site_revision": int(record.get("site_revision") or 0),
        }

    def release_scope(self, *, site_key: str, batch_id: str = "", event: str = "released") -> int:
        """Close indexed work items for an explicitly released execution scope."""

        with self._lock:
            index = read_json(self.index_path)
            records = index.get("records") if isinstance(index.get("records"), dict) else {}
            updated = 0
            for work_item_id, record in list(records.items()):
                if not isinstance(record, dict) or str(record.get("site_key") or "") != str(site_key or ""):
                    continue
                if batch_id and str(record.get("batch_id") or "") != str(batch_id):
                    continue
                if str(record.get("state") or "") in {"completed", "released", "cancelled"}:
                    continue
                records[work_item_id] = {
                    **record,
                    "state": "released",
                    "site_revision": int(record.get("site_revision") or 0) + 1,
                    "updated_at": now_iso(),
                }
                self._append_event(event, records[work_item_id])
                updated += 1
            if updated:
                write_json(self.index_path, {"updated_at": now_iso(), "records": records})
            return updated

    def _discover(self, work_item_id: str) -> dict[str, Any] | None:
        """Backfill legacy payloads without relying on browser session state."""

        for path in self.workspace.glob("agent_bridge/browser/**/payload.json"):
            payload = read_json(path)
            if work_item_id_from_payload(payload) == work_item_id:
                return self.register(path, event="discovered")
        return None

    def _load_payload(self, payload_path: Path | str) -> tuple[dict[str, Any], Path]:
        path = Path(str(payload_path or "")).expanduser().resolve()
        payload = read_json(path)
        if not path.is_file() or not isinstance(payload, dict) or not payload:
            raise ValueError("persisted work item is unavailable")
        return payload, path

    def _append_event(self, event: str, record: dict[str, Any]) -> None:
        row = {"at": now_iso(), "event": event, **record}
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
