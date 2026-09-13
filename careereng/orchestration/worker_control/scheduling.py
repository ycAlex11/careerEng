"""Durable native-site capacity admission, independent of business wait reasons."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .registry import NativeWorkerRegistry
from .lifecycle import is_terminal_work


def validate_wait_decision(decision: dict[str, Any]) -> dict[str, str]:
    fields = {"slot_policy", "reason", "evidence", "resume_condition"}
    if set(decision) != fields:
        raise ValueError("wait decision requires slot_policy, reason, evidence, resume_condition")
    if any(not isinstance(decision[key], str) or not decision[key].strip() for key in fields):
        raise ValueError("wait decision fields must be nonempty strings")
    normalized = {key: decision[key].strip() for key in fields}
    if normalized["slot_policy"] not in {"retain", "release"}:
        raise ValueError("slot_policy must be retain or release")
    return normalized


def execution_admitted(worker: dict[str, Any]) -> bool:
    if not worker.get("slot_state"):
        return True
    return (
        worker.get("slot_state") == "held"
        and worker.get("desired_state") == "running"
        and worker.get("work_state") in {"queued", "running"}
    )


class SiteCapacityScheduler:
    def __init__(self, workspace: Path | str, *, limit: int):
        self.workspace = Path(workspace)
        self.registry = NativeWorkerRegistry(workspace)
        self.limit = max(1, int(limit))

    def enroll(self, work_item_id: str) -> dict[str, Any]:
        with self.registry._lock:
            worker = self.registry.get(work_item_id=work_item_id)
            if not worker:
                raise ValueError("native worker binding required")
            if worker.get("worker_kind") != "site" or worker.get("slot_state"):
                return worker
            sequence = self._next_sequence()
            return self.registry.update(work_item_id, slot_state="queued", queue_sequence=sequence)

    def _next_sequence(self) -> int:
        return max((int(row.get("queue_sequence") or 0) for row in self.registry.list()), default=0) + 1

    def resume(self, work_item_id: str) -> dict[str, Any]:
        with self.registry._lock:
            worker = self.registry.get(work_item_id=work_item_id)
            if not worker.get("slot_state") or is_terminal_work(str(worker.get("work_state") or "")):
                return worker
            if worker.get("slot_state") == "held" and worker.get("work_state") in {"queued", "running"} and not worker.get("wait_decision"):
                return worker
            changes: dict[str, Any] = {"work_state": "queued", "wait_decision": {}}
            if worker.get("slot_state") != "held":
                changes["slot_state"] = "queued"
                if worker.get("slot_state") != "queued":
                    changes["queue_sequence"] = self._next_sequence()
            return self.registry.update(work_item_id, **changes)

    def report(self, work_item_id: str, *, changes: dict[str, Any],
               decision: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = validate_wait_decision(decision) if decision is not None else None
        with self.registry._lock:
            worker = self.registry.get(work_item_id=work_item_id)
            if not worker:
                raise ValueError("native worker binding required")
            expected = changes.get("expected_control_epoch")
            if expected is None or int(expected) != int(worker.get("control_epoch") or 0):
                raise ValueError("obsolete control epoch")
            if normalized:
                if changes.get("work_state") != "waiting_user":
                    raise ValueError("wait decision requires waiting_user state")
                if is_terminal_work(str(worker.get("work_state") or "")) or worker.get("desired_state") != "running":
                    raise ValueError("wait decision cannot change stopped work")
                if normalized["slot_policy"] == "release" and changes.get("runtime_state") not in {"suspended", "terminal", "faulted"}:
                    raise ValueError("release requires a quiescent worker report")
                if normalized["slot_policy"] == "release" and worker.get("inflight_operations"):
                    raise ValueError("release requires outstanding operations to finish")
                if worker.get("slot_state") and normalized["slot_policy"] == "retain" and worker["slot_state"] != "held":
                    raise ValueError("released capacity must be reacquired through resume")
                changes = {**changes, "wait_decision": normalized}
                if worker.get("slot_state") and normalized["slot_policy"] == "release":
                    changes["slot_state"] = "released"
            if changes.get("work_state") == "running" and not execution_admitted(worker):
                raise ValueError("worker must be admitted through the resume queue before execution")
            return self.registry.update(work_item_id, **changes)

    def reconcile(self) -> dict[str, Any]:
        from careereng.orchestration.agent_protocol.work_item_store import WorkItemStore
        from careereng.orchestration.engine.site_work_items import SiteWorkItem, SiteWorkItemScheduler

        with self.registry._lock:
            durable = {row["work_item_id"]: row for row in WorkItemStore(self.workspace).list_records()}
            workers = self.registry.list()
            for worker in workers:
                if not worker.get("slot_state"):
                    continue
                record = durable.get(worker["work_item_id"], {})
                if record.get("state") in {"completed", "cancelled"}:
                    worker = self.registry.update(worker["work_item_id"], work_state=record["state"],
                                                  control_epoch=max(int(worker.get("control_epoch") or 0), int(record.get("control_epoch") or 0)))
                terminal = is_terminal_work(str(worker.get("work_state") or ""))
                stopped = worker.get("desired_state") in {"paused", "cancelled"} and worker.get("runtime_state") in {"detached", "suspended", "terminal", "faulted"}
                if terminal or stopped:
                    self.registry.update(worker["work_item_id"], slot_state="released")
            workers = self.registry.list()
            held = [row for row in workers if row.get("slot_state") == "held"]
            occupied_sites = {row.get("site_key") for row in held}
            queue = sorted(
                (row for row in workers if row.get("slot_state") == "queued"
                 and row.get("desired_state") == "running"
                 and row.get("work_state") in {"queued", "running"}),
                key=lambda row: int(row.get("queue_sequence") or 0),
            )
            available = self.limit - len(held)
            if available > 0:
                planner = SiteWorkItemScheduler(worker_limit=available)
                for worker in queue:
                    if worker.get("site_key") not in occupied_sites:
                        planner.enqueue(SiteWorkItem(site_key=worker["site_key"], batch_id=worker["batch_id"], payload=worker))
                for item in planner.claim_ready():
                    held.append(self.registry.update(item.payload["work_item_id"], slot_state="held"))
            held_ids = {row["work_item_id"] for row in held}
            return {"limit": self.limit, "held": [row["work_item_id"] for row in held],
                    "queued": [row["work_item_id"] for row in queue if row["work_item_id"] not in held_ids]}
