"""Durable native-agent actions planned by CareerEng and executed by a supervisor."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from careereng.utils import ensure_dir, make_id, now_iso, read_json, write_json
from .registry import NativeWorkerRegistry
from .lifecycle import is_terminal_work
from .liveness import obsolete_recovery
from .scheduling import execution_admitted
from careereng.platform.persistence.mutex import workspace_mutex


class WorkerActionKind(StrEnum):
    SPAWN = "spawn"
    SEND = "send"
    INTERRUPT = "interrupt"
    RESUME = "resume"
    CLOSE = "close"
    PROBE = "probe"


class WorkerActionStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    APPLIED = "applied"
    FAILED = "failed"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class WorkerAction:
    action_id: str
    kind: WorkerActionKind
    agent_id: str
    work_item_id: str
    site_key: str
    batch_id: str
    control_epoch: int
    payload: dict[str, Any]
    status: WorkerActionStatus = WorkerActionStatus.PENDING
    created_at: str = ""
    updated_at: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "kind": self.kind.value,
            "agent_id": self.agent_id,
            "work_item_id": self.work_item_id,
            "site_key": self.site_key,
            "batch_id": self.batch_id,
            "control_epoch": self.control_epoch,
            "payload": dict(self.payload),
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorkerAction":
        return cls(
            action_id=str(payload.get("action_id") or ""),
            kind=WorkerActionKind(str(payload.get("kind") or "send")),
            agent_id=str(payload.get("agent_id") or ""),
            work_item_id=str(payload.get("work_item_id") or ""),
            site_key=str(payload.get("site_key") or ""),
            batch_id=str(payload.get("batch_id") or ""),
            control_epoch=int(payload.get("control_epoch") or 0),
            payload=dict(payload.get("payload") or {}),
            status=WorkerActionStatus(str(payload.get("status") or "pending")),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            error=str(payload.get("error") or ""),
        )


class WorkerActionStore:
    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.path = ensure_dir(Path(workspace) / "sessions" / "worker_actions") / "actions.json"
        self._lock = workspace_mutex(self.path)

    def enqueue(self, action: WorkerAction) -> WorkerAction:
        with self._lock:
            data = self._load()
            existing = next((row for row in data["actions"] if row.get("action_id") == action.action_id), None)
            if existing is not None:
                return WorkerAction.from_dict(existing)
            equivalent = next(
                (
                    row
                    for row in data["actions"]
                    if row.get("work_item_id") == action.work_item_id
                    and row.get("kind") == action.kind.value
                    and int(row.get("control_epoch") or 0) == action.control_epoch
                    and dict(row.get("payload") or {}) == action.payload
                    and row.get("status") in {
                        WorkerActionStatus.PENDING.value,
                        WorkerActionStatus.CLAIMED.value,
                    }
                ),
                None,
            )
            if equivalent is not None:
                return WorkerAction.from_dict(equivalent)
            data["actions"].append(action.as_dict())
            write_json(self.path, data)
            return action

    def pending(self, *, batch_id: str = "", site_key: str = "") -> list[WorkerAction]:
        with self._lock:
            rows = [WorkerAction.from_dict(row) for row in self._load()["actions"]]
        statuses = {row.action_id: row.status for row in rows}
        registry = NativeWorkerRegistry(self.workspace)
        for row in rows:
            if row.status not in {WorkerActionStatus.PENDING, WorkerActionStatus.CLAIMED}:
                continue
            worker = registry.get(work_item_id=row.work_item_id)
            if not worker:
                continue
            stale = row.control_epoch != int(worker.get("control_epoch") or 0)
            stale = stale or bool(row.agent_id and row.agent_id != worker.get("agent_id"))
            stale = stale or obsolete_recovery(worker, row.payload)
            if row.agent_id:
                latest = registry.get(agent_id=row.agent_id)
                stale = stale or latest.get("work_item_id") != row.work_item_id
            terminal_action = is_terminal_work(str(worker.get("work_state") or "")) and row.kind in {
                WorkerActionKind.SPAWN, WorkerActionKind.SEND, WorkerActionKind.RESUME,
            }
            if stale or terminal_action:
                self.transition(row.action_id, status=WorkerActionStatus.SUPERSEDED,
                                error="obsolete control epoch or terminal work item")
                self.supersede_dependents(row.action_id, error="obsolete prerequisite")
                statuses[row.action_id] = WorkerActionStatus.SUPERSEDED
        return [
            row for row in rows
            if statuses[row.action_id] == WorkerActionStatus.PENDING
            and (not batch_id or row.batch_id == batch_id)
            and (not site_key or row.site_key == site_key)
            and (row.kind not in {WorkerActionKind.SPAWN, WorkerActionKind.SEND, WorkerActionKind.RESUME}
                 or execution_admitted(registry.get(work_item_id=row.work_item_id)))
            and (
                not str(row.payload.get("depends_on_action_id") or "")
                or statuses.get(str(row.payload.get("depends_on_action_id") or "")) == WorkerActionStatus.APPLIED
            )
        ]

    def for_command(self, command_id: str) -> list[WorkerAction]:
        normalized = str(command_id or "").strip()
        if not normalized:
            return []
        with self._lock:
            rows = [WorkerAction.from_dict(row) for row in self._load()["actions"]]
        return [row for row in rows if str(row.payload.get("command_id") or "") == normalized]

    def unsettled(self, work_item_id: str) -> list[WorkerAction]:
        self.pending()
        with self._lock:
            return [WorkerAction.from_dict(row) for row in self._load()["actions"]
                    if row.get("work_item_id") == work_item_id
                    and row.get("status") in {"pending", "claimed"}]

    def supersede_dependents(self, action_id: str, *, error: str = "") -> list[WorkerAction]:
        normalized = str(action_id or "").strip()
        if not normalized:
            return []
        with self._lock:
            rows = [WorkerAction.from_dict(row) for row in self._load()["actions"]]
        updated = []
        for row in rows:
            if (
                row.status == WorkerActionStatus.PENDING
                and str(row.payload.get("depends_on_action_id") or "") == normalized
            ):
                updated.append(
                    self.transition(
                        row.action_id,
                        status=WorkerActionStatus.SUPERSEDED,
                        error=error or "prerequisite action failed",
                    )
                )
        return updated

    def transition(self, action_id: str, *, status: WorkerActionStatus | str, error: str = "") -> WorkerAction:
        normalized = status if isinstance(status, WorkerActionStatus) else WorkerActionStatus(str(status))
        with self._lock:
            data = self._load()
            row = next((item for item in data["actions"] if item.get("action_id") == action_id), None)
            if row is None:
                raise KeyError(f"worker action not found: {action_id}")
            current = WorkerActionStatus(str(row.get("status") or "pending"))
            allowed = {
                WorkerActionStatus.PENDING: {WorkerActionStatus.CLAIMED, WorkerActionStatus.FAILED, WorkerActionStatus.SUPERSEDED},
                WorkerActionStatus.CLAIMED: {WorkerActionStatus.APPLIED, WorkerActionStatus.FAILED, WorkerActionStatus.SUPERSEDED},
            }
            if normalized != current and normalized not in allowed.get(current, set()):
                raise ValueError(f"invalid worker action transition: {current.value} -> {normalized.value}")
            updated = replace(WorkerAction.from_dict(row), status=normalized, updated_at=now_iso(), error=str(error or ""))
            row.clear()
            row.update(updated.as_dict())
            write_json(self.path, data)
            return updated

    def get(self, action_id: str) -> WorkerAction:
        with self._lock:
            row = next(
                (item for item in self._load()["actions"] if item.get("action_id") == action_id),
                None,
            )
        if row is None:
            raise KeyError(f"worker action not found: {action_id}")
        return WorkerAction.from_dict(row)

    def acknowledge(self, action_id: str, *, applied: bool, error: str = "") -> WorkerAction:
        """Record an idempotent supervisor receipt for one action."""

        target = WorkerActionStatus.APPLIED if applied else WorkerActionStatus.FAILED
        current = self.get(action_id)
        if current.status == target:
            return current
        if current.status == WorkerActionStatus.PENDING:
            current = self.transition(action_id, status=WorkerActionStatus.CLAIMED)
        if current.status != WorkerActionStatus.CLAIMED:
            raise ValueError(
                f"worker action cannot be acknowledged from {current.status.value}"
            )
        return self.transition(action_id, status=target, error=error)

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        payload = read_json(self.path)
        rows = payload.get("actions") if isinstance(payload.get("actions"), list) else []
        return {"actions": [dict(row) for row in rows if isinstance(row, dict)]}


def create_worker_action(*, kind: WorkerActionKind | str, agent_id: str, work_item_id: str,
                         site_key: str, batch_id: str, control_epoch: int,
                         payload: dict[str, Any] | None = None, action_id: str = "") -> WorkerAction:
    now = now_iso()
    return WorkerAction(
        action_id=str(action_id or make_id("worker_action")),
        kind=kind if isinstance(kind, WorkerActionKind) else WorkerActionKind(str(kind)),
        agent_id=str(agent_id or ""), work_item_id=str(work_item_id or ""),
        site_key=str(site_key or ""), batch_id=str(batch_id or ""),
        control_epoch=max(0, int(control_epoch or 0)), payload=dict(payload or {}),
        created_at=now, updated_at=now,
    )
