"""Durable bindings between CareerEng work items and native desktop agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.utils import ensure_dir, make_id, now_iso, read_json, write_json

from .models import AgentRuntimeState, BrowserResourceState, WorkerDesiredState, WorkItemRuntimeState
from .lifecycle import is_terminal_work
from careereng.platform.persistence.mutex import workspace_mutex


class NativeWorkerRegistry:
    def __init__(self, workspace: Path | str):
        self.path = ensure_dir(Path(workspace) / "sessions" / "native_workers") / "workers.json"
        self._lock = workspace_mutex(self.path)

    def plan(self, *, work_item_id: str, site_key: str, batch_id: str,
             worker_kind: str = "site", control_epoch: int = 0,
             worker_session_id: str = "", agent_id: str = "",
             runtime_state: str = "detached") -> dict[str, Any]:
        """Persist a new work-item binding, optionally reusing a session agent."""

        if not all(str(value or "").strip() for value in (work_item_id, batch_id)):
            raise ValueError("planned native worker requires work_item_id and batch_id")
        with self._lock:
            data = self._load()
            existing = next((row for row in data["workers"] if row.get("work_item_id") == work_item_id), None)
            if existing is not None:
                return dict(existing)
            now = now_iso()
            payload = {
                "agent_id": str(agent_id or ""),
                "work_item_id": str(work_item_id),
                "worker_session_id": str(worker_session_id or ""),
                "site_key": str(site_key or ""),
                "batch_id": str(batch_id),
                "worker_kind": str(worker_kind or "site"),
                "parent_agent_id": "",
                "desired_state": "running",
                "runtime_state": AgentRuntimeState(str(runtime_state or "detached")).value,
                "work_state": "queued",
                "browser_state": "absent",
                "browser_policy": "unchanged",
                "control_epoch": max(0, int(control_epoch or 0)),
                "revision": 1,
                "registered_at": now if agent_id else "",
                "updated_at": now,
                "last_heartbeat_at": "",
            }
            data["workers"].append(payload)
            write_json(self.path, data)
            return dict(payload)

    def register(self, *, agent_id: str, work_item_id: str, site_key: str, batch_id: str,
                 worker_kind: str = "site", parent_agent_id: str = "",
                 control_epoch: int = 0, worker_session_id: str = "") -> dict[str, Any]:
        if not all(str(value or "").strip() for value in (agent_id, work_item_id, batch_id)):
            raise ValueError("native worker requires agent_id, work_item_id, and batch_id")
        with self._lock:
            data = self._load()
            existing = next((row for row in data["workers"] if row.get("work_item_id") == work_item_id), None)
            now = now_iso()
            if existing is not None and str(existing.get("agent_id") or "") == str(agent_id):
                if control_epoch and int(existing.get("control_epoch") or 0) != int(control_epoch):
                    raise ValueError("obsolete control epoch")
                if parent_agent_id:
                    existing["parent_agent_id"] = str(parent_agent_id)
                existing["updated_at"] = now
                existing["last_heartbeat_at"] = now
                write_json(self.path, data)
                return dict(existing)
            payload = {
                "agent_id": str(agent_id), "work_item_id": str(work_item_id),
                "worker_session_id": str(worker_session_id or existing.get("worker_session_id") or "") if existing else str(worker_session_id or ""),
                "site_key": str(site_key or ""), "batch_id": str(batch_id),
                "worker_kind": str(worker_kind or "site"),
                "parent_agent_id": str(parent_agent_id or ""),
                "desired_state": "running", "runtime_state": "running",
                "work_state": "running", "browser_state": "absent",
                "control_epoch": max(0, int(control_epoch or 0)),
                "revision": int(existing.get("revision") or 0) + 1 if existing else 1,
                "registered_at": str(existing.get("registered_at") or now) if existing else now,
                "updated_at": now, "last_heartbeat_at": now,
            }
            if existing:
                for key in ("slot_state", "queue_sequence", "wait_decision", "launch_spec", "browser_policy"):
                    if key in existing:
                        payload[key] = existing[key]
            if existing is None:
                data["workers"].append(payload)
            else:
                existing.clear(); existing.update(payload)
            write_json(self.path, data)
            return dict(payload)

    def update(self, work_item_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            row = next((item for item in data["workers"] if item.get("work_item_id") == work_item_id), None)
            if row is None:
                raise KeyError(f"native worker not found: {work_item_id}")
            expected_epoch = changes.pop("expected_control_epoch", None)
            if expected_epoch is not None and int(expected_epoch) != int(row.get("control_epoch") or 0):
                raise ValueError("obsolete control epoch")
            if "control_epoch" in changes and int(changes["control_epoch"]) < int(row.get("control_epoch") or 0):
                raise ValueError("control epoch cannot move backwards")
            immutable = {"agent_id", "work_item_id", "worker_session_id", "site_key", "batch_id", "worker_kind", "parent_agent_id", "registered_at"}
            validators = {
                "desired_state": WorkerDesiredState,
                "runtime_state": AgentRuntimeState,
                "work_state": WorkItemRuntimeState,
                "browser_state": BrowserResourceState,
            }
            normalized_changes: dict[str, Any] = {}
            for key, value in changes.items():
                if key not in immutable and value is not None:
                    normalized_changes[key] = validators[key](str(value)).value if key in validators else value
            heartbeat = bool(normalized_changes.pop("heartbeat", False))
            if is_terminal_work(str(row.get("work_state") or "")):
                normalized_changes.pop("work_state", None)
            changed = any(row.get(key) != value for key, value in normalized_changes.items())
            if not changed and not heartbeat:
                return dict(row)
            if "control_epoch" in normalized_changes and normalized_changes["control_epoch"] != row.get("control_epoch"):
                row.update(inflight_operations={}, suspect_checks=0, last_probe_at="")
            row.update(normalized_changes)
            row["revision"] = int(row.get("revision") or 0) + 1
            row["updated_at"] = now_iso()
            if heartbeat:
                row["last_heartbeat_at"] = row["updated_at"]
                if row.get("runtime_state") == "running" and row.get("work_state") == "running":
                    row["activity_revision"] = int(row.get("activity_revision") or 0) + 1
                    row["suspect_checks"] = 0
                    row["last_probe_at"] = ""
                    if row.get("interrupt_is_recovery"):
                        row.update(interrupt_ack_started_at="", control_state="", interrupt_is_recovery=False)
            write_json(self.path, data)
            return dict(row)

    def record_activity(self, work_item_id: str, *, expected_control_epoch: int,
                        operation_id: str = "", completed: bool = False,
                        progress: bool = False) -> str:
        with self._lock:
            data = self._load()
            row = next((item for item in data["workers"] if item.get("work_item_id") == work_item_id), None)
            if not row or int(row.get("control_epoch") or 0) != expected_control_epoch:
                return ""
            if row.get("desired_state") != "running" or (not completed and row.get("work_state") not in {"running", "queued"}):
                return ""
            operations = dict(row.get("inflight_operations") or {})
            if completed and operation_id not in operations:
                return ""
            stamp = now_iso()
            token = operation_id or make_id("activity")
            if completed:
                operations.pop(token)
            else:
                operations[token] = stamp
            row.update(inflight_operations=operations, last_activity_at=stamp,
                       activity_revision=int(row.get("activity_revision") or 0) + 1,
                       revision=int(row.get("revision") or 0) + 1,
                       updated_at=stamp, suspect_checks=0, last_probe_at="", recovery_attempts=0)
            if progress:
                row["last_progress_at"] = stamp
            if row.get("interrupt_is_recovery"):
                row.update(interrupt_ack_started_at="", control_state="", interrupt_is_recovery=False)
            write_json(self.path, data)
            return token

    def get(self, *, work_item_id: str = "", agent_id: str = "") -> dict[str, Any]:
        with self._lock:
            rows = self._load()["workers"]
        row = next((item for item in reversed(rows) if (work_item_id and item.get("work_item_id") == work_item_id)
                    or (agent_id and item.get("agent_id") == agent_id)), None)
        return dict(row) if row else {}

    def latest_for_session(self, worker_session_id: str) -> dict[str, Any]:
        normalized = str(worker_session_id or "").strip()
        if not normalized:
            return {}
        with self._lock:
            rows = self._load()["workers"]
        row = next((item for item in reversed(rows) if item.get("worker_session_id") == normalized), None)
        return dict(row) if row else {}

    def list(self, *, batch_id: str = "", active_only: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._load()["workers"]
        terminal = {"completed", "cancelled", "failed"}
        return [dict(row) for row in rows if (not batch_id or row.get("batch_id") == batch_id)
                and (not active_only or row.get("work_state") not in terminal)]

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        payload = read_json(self.path)
        rows = payload.get("workers") if isinstance(payload.get("workers"), list) else []
        return {"workers": [dict(row) for row in rows if isinstance(row, dict)]}
