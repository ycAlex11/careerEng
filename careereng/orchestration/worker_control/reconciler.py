"""Pure desired/observed reconciliation for native worker lifecycles."""

from __future__ import annotations

from .actions import WorkerActionKind, create_worker_action
from .models import BrowserResourcePolicy


class WorkerLifecycleReconciler:
    def plan(self, worker: dict, *, resource_policy: BrowserResourcePolicy | str = BrowserResourcePolicy.UNCHANGED):
        desired = str(worker.get("desired_state") or "running")
        runtime = str(worker.get("runtime_state") or "detached")
        work_state = str(worker.get("work_state") or "queued")
        policy = resource_policy if isinstance(resource_policy, BrowserResourcePolicy) else BrowserResourcePolicy(str(resource_policy))
        common = {
            "agent_id": str(worker.get("agent_id") or ""),
            "work_item_id": str(worker.get("work_item_id") or ""),
            "site_key": str(worker.get("site_key") or ""),
            "batch_id": str(worker.get("batch_id") or ""),
            "control_epoch": int(worker.get("control_epoch") or 0),
        }
        action_context = {
            "resource_policy": policy.value,
            "worker_revision": int(worker.get("revision") or 0),
        }
        if desired == "running" and work_state not in {"completed", "cancelled", "failed", "waiting_user", "paused"} and runtime in {"detached", "suspended", "terminal", "faulted"}:
            kind = WorkerActionKind.RESUME if common["agent_id"] else WorkerActionKind.SPAWN
            return [create_worker_action(kind=kind, payload=action_context, **common)]
        if desired == "paused" and runtime in {"starting", "running"}:
            return [create_worker_action(kind=WorkerActionKind.INTERRUPT, payload={**action_context, "reason": "pause"}, **common)]
        if desired == "paused" and runtime == "suspended":
            if policy == BrowserResourcePolicy.RELEASE:
                return [create_worker_action(kind=WorkerActionKind.CLOSE, payload=action_context, **common)]
            return []
        if desired == "cancelled" and runtime not in {"terminal", "detached"}:
            kind = WorkerActionKind.INTERRUPT if runtime in {"starting", "running", "quiescing"} else WorkerActionKind.CLOSE
            return [create_worker_action(kind=kind, payload={**action_context, "reason": "cancel", "resource_policy": BrowserResourcePolicy.RELEASE.value}, **common)]
        if work_state == "completed" and str(worker.get("worker_kind") or "site") == "site":
            return []
        if work_state in {"completed", "cancelled", "failed"} and runtime not in {"terminal", "detached"}:
            return [create_worker_action(kind=WorkerActionKind.CLOSE, payload={**action_context, "resource_policy": BrowserResourcePolicy.RELEASE.value}, **common)]
        return []
