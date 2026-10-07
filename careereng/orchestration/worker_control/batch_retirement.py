"""Fence retired batch execution while preserving historical site outcomes."""

from careereng.orchestration.agent_protocol.work_item_store import WorkItemStore
from careereng.orchestration.worker_control.actions import WorkerActionStore
from careereng.orchestration.worker_control.registry import NativeWorkerRegistry
from careereng.orchestration.worker_control.reconciler import WorkerLifecycleReconciler
from careereng.utils import now_iso


def retire_site_workers(workspace, batch_id, site_key):
    registry = NativeWorkerRegistry(workspace)
    items = WorkItemStore(workspace)
    with registry._lock:
        workers = [row for row in registry.list(batch_id=batch_id) if row.get("site_key") == site_key]
        if workers and all(row.get("execution_retired") for row in workers):
            return
        records = items.revoke_scope(site_key=site_key, batch_id=batch_id, state="cancelling", event="site_execution_retired")
        actions = WorkerActionStore(workspace)
        for worker in workers:
            if worker.get("execution_retired"):
                continue
            epoch = max([int(worker.get("control_epoch") or 0) + 1] +
                        [int(row.get("control_epoch") or 0) for row in records if row.get("work_item_id") == worker["work_item_id"]])
            updated = registry.update(worker["work_item_id"], desired_state="cancelled", work_state="cancelled",
                                      browser_policy="release", slot_state="released", control_epoch=epoch,
                                      execution_retired=True, execution_archived_at=now_iso())
            for action in WorkerLifecycleReconciler().plan(updated, resource_policy="release"):
                actions.enqueue(action)
        actions.pending()


def retire_batch_workers(workspace, batch_id):
    sites = {row.get("site_key") for row in NativeWorkerRegistry(workspace).list(batch_id=batch_id)}
    for site_key in sites:
        retire_site_workers(workspace, batch_id, site_key)


def archive_previous_batches(flow, workspace, session_id, *, retire=None):
    store = flow.job_store
    for batch in store.list_batches(session_id=session_id):
        if batch.get("archived_at") and not batch.get("runtime_cleanup_pending"):
            continue
        batch_id = batch["batch_id"]
        retire_batch_workers(workspace, batch_id)
        closed = retire(batch) if retire else flow.cancel_batch(batch_id=batch_id, reason="superseded_by_new_batch")
        closed["archived_at"] = now_iso()
        closed["resume_allowed"] = False
        closed["runtime_cleanup_pending"] = False
        for site in closed.get("sites", {}).values():
            site["execution_outcome"] = "completed" if site.get("status") == "completed" else "incomplete"
            site.pop("continuation", None)
        store.save_batch(closed)
