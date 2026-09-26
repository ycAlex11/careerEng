"""Fence retired batch execution while preserving historical site outcomes."""

from careereng.orchestration.agent_protocol.work_item_store import WorkItemStore
from careereng.orchestration.worker_control.actions import WorkerActionStore, WorkerActionStatus
from careereng.orchestration.worker_control.registry import NativeWorkerRegistry
from careereng.utils import now_iso


def retire_batch_workers(workspace, batch_id):
    registry = NativeWorkerRegistry(workspace)
    items = WorkItemStore(workspace)
    for worker in registry.list(batch_id=batch_id):
        items.revoke_scope(site_key=worker["site_key"], batch_id=batch_id,
                           state="cancelling", event="batch_retired")
        registry.update(worker["work_item_id"], desired_state="cancelled",
                        work_state="cancelled", slot_state="released",
                        control_epoch=int(worker.get("control_epoch") or 0) + 1)
    actions = WorkerActionStore(workspace)
    for action in actions.pending(batch_id=batch_id):
        actions.transition(action.action_id, status=WorkerActionStatus.SUPERSEDED,
                           error="batch execution retired")


def archive_previous_batches(flow, workspace, session_id, *, retire=None):
    store = flow.job_store
    for batch in store.list_batches(session_id=session_id):
        if batch.get("archived_at"):
            continue
        batch_id = batch["batch_id"]
        retire_batch_workers(workspace, batch_id)
        closed = retire(batch) if retire else flow.cancel_batch(batch_id=batch_id, reason="superseded_by_new_batch")
        closed["archived_at"] = now_iso()
        closed["resume_allowed"] = False
        for site in closed.get("sites", {}).values():
            site["execution_outcome"] = "completed" if site.get("status") == "completed" else "incomplete"
            site.pop("continuation", None)
        store.save_batch(closed)
