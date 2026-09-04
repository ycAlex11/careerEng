"""Generic recovery-batch construction from durable workflow checkpoints."""

from __future__ import annotations

from typing import Any

from careereng.career.applications.planning_store import JobPlanningStore
from careereng.career.resume.batch_snapshot import clone_batch_resume_snapshot
from careereng.utils import now_iso


class BatchCheckpointRecovery:
    """Clone durable phase/item state while excluding transient runtime ownership."""

    def __init__(self, *, job_store: Any, site_store: Any):
        self.job_store = job_store
        self.site_store = site_store
        self.planning_store = JobPlanningStore(job_store.workspace)

    def create(
        self,
        *,
        source_batch_id: str,
        site_key: str,
        session_id: str,
        turn_id: str,
        user_message: str,
        command_id: str = "",
    ) -> dict[str, Any]:
        existing = self._existing_recovery(
            source_batch_id=source_batch_id,
            site_key=site_key,
            command_id=command_id,
        )
        if existing:
            return existing
        source = self.job_store.load_batch(source_batch_id)
        if not source:
            raise FileNotFoundError(f"source job batch not found: {source_batch_id}")
        source_sites = source.get("sites") if isinstance(source.get("sites"), dict) else {}
        source_site = source_sites.get(site_key)
        if not isinstance(source_site, dict):
            raise ValueError(f"site is not in source batch: {site_key}")
        phase = self._restart_phase(source=source, site=source_site)
        if not phase:
            raise ValueError("source batch has no unfinished durable checkpoint")
        recovered_site = self._recovered_site_row(source_site, phase=phase, source_batch_id=source_batch_id)
        batch = self.job_store.create_batch(
            session_id=session_id,
            turn_id=turn_id,
            user_message=user_message,
            apply_requested=bool(source.get("apply_requested")),
            operation=str(source.get("operation") or "job_search"),
            sites=[recovered_site],
            execution_backend=str(source.get("execution_backend") or "provider"),
        )
        batch_id = str(batch.get("batch_id") or "")
        self.site_store.clone_run_checkpoint(
            site_key,
            source_batch_id=source_batch_id,
            target_batch_id=batch_id,
            session_id=session_id,
            turn_id=turn_id,
        )
        plan = self.planning_store.clone_apply_plan(
            source_batch_id=source_batch_id,
            target_batch_id=batch_id,
            site_key=site_key,
        )
        if plan:
            apply = dict(recovered_site.get("apply") or {})
            apply["plan"] = {
                "plan_id": str(plan.get("plan_id") or ""),
                "snapshot_id": str(plan.get("snapshot_id") or ""),
                "path": str(plan.get("path") or ""),
                "counts": dict(plan.get("counts") or {}),
                "recovered_from_plan_id": str(plan.get("recovered_from_plan_id") or ""),
            }
            recovered_site["apply"] = apply
        resume_snapshot = self._clone_resume_snapshot(
            source.get("resume_snapshot"),
            batch_id=batch_id,
            site_key=site_key,
        )
        batch = self.job_store.load_batch(batch_id)
        batch["sites"][site_key] = recovered_site
        batch["status"] = "waiting_user"
        batch["recovery"] = {
            "kind": "checkpoint_recovery",
            "source_batch_id": source_batch_id,
            "source_status": str(source.get("status") or ""),
            "restart_phase": phase,
            "command_id": str(command_id or ""),
            "created_at": now_iso(),
        }
        if resume_snapshot:
            batch["resume_snapshot"] = resume_snapshot
        batch = self.job_store.save_batch(batch)
        self.site_store.save_browser_session(
            site_key,
            {
                "browser_status": "waiting_user",
                "pending_action": "checkpoint_recovery_ready",
                "resume_phase": phase,
                "last_known_url": str(recovered_site.get("current_url") or recovered_site.get("entry_url") or ""),
                "active_run_id": "",
                "last_browser_pid": 0,
                "agent_bridge_session_id": "",
                "agent_bridge_batch_id": "",
                "agent_bridge_turn_id": "",
                "agent_bridge_payload_path": "",
                "phase_session_path": "",
                "codex_turn_id": "",
                "codex_worker_status": "waiting_user",
            },
        )
        self.job_store.append_event(
            "batch.checkpoint_recovered",
            {
                "batch_id": batch_id,
                "source_batch_id": source_batch_id,
                "site_key": site_key,
                "restart_phase": phase,
                "active_target_job_id": str((recovered_site.get("apply") or {}).get("active_target_job_id") or ""),
                "command_id": str(command_id or ""),
            },
        )
        return batch

    def _existing_recovery(self, *, source_batch_id: str, site_key: str, command_id: str) -> dict[str, Any]:
        if not command_id:
            return {}
        for batch in self.job_store.list_batches():
            recovery = batch.get("recovery") if isinstance(batch.get("recovery"), dict) else {}
            sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
            if (
                str(recovery.get("source_batch_id") or "") == source_batch_id
                and str(recovery.get("command_id") or "") == command_id
                and site_key in sites
            ):
                return batch
        return {}

    @staticmethod
    def _restart_phase(*, source: dict[str, Any], site: dict[str, Any]) -> str:
        if str(site.get("status") or "") == "completed":
            return ""
        retrieve = site.get("retrieve") if isinstance(site.get("retrieve"), dict) else {}
        apply = site.get("apply") if isinstance(site.get("apply"), dict) else {}
        retrieve_status = str(retrieve.get("status") or "")
        apply_status = str(apply.get("status") or "")
        if retrieve_status == "done" and bool(source.get("apply_requested")):
            return "" if apply_status == "done" else "apply"
        phase = str(site.get("current_phase") or "").strip()
        if phase:
            return phase
        return "job_retrieval"

    @staticmethod
    def _recovered_site_row(site: dict[str, Any], *, phase: str, source_batch_id: str) -> dict[str, Any]:
        recovered = dict(site)
        retrieve = dict(recovered.get("retrieve") or {})
        apply = dict(recovered.get("apply") or {})
        if phase == "job_retrieval" and str(retrieve.get("status") or "") != "done":
            retrieve["status"] = "running"
            retrieve.pop("reason_tag", None)
        if phase == "apply":
            apply["status"] = "running"
            apply.pop("reason_tag", None)
        recovered.update(
            {
                "status": "waiting_user",
                "reason_tag": "checkpoint_recovery_ready",
                "message": f"Ready to continue from {phase} using the durable checkpoint.",
                "current_phase": phase,
                "retrieve": retrieve,
                "apply": apply,
                "continuation": {
                    "kind": "checkpoint_recovery",
                    "source_batch_id": source_batch_id,
                    "phase": phase,
                    "last_known_url": str(recovered.get("current_url") or recovered.get("entry_url") or ""),
                    "apply_target_job_ids": [str(apply.get("active_target_job_id") or "")]
                    if str(apply.get("active_target_job_id") or "")
                    else [],
                },
            }
        )
        return recovered

    def _clone_resume_snapshot(
        self,
        source_snapshot: object,
        *,
        batch_id: str,
        site_key: str,
    ) -> dict[str, Any]:
        source = source_snapshot if isinstance(source_snapshot, dict) else {}
        if not source:
            return {}
        return clone_batch_resume_snapshot(
            workspace=self.job_store.workspace,
            source=source,
            target_batch_id=batch_id,
            site_keys=[site_key],
        )
