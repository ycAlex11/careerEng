"""Debug helpers for applying a small sample from an existing job batch."""

from __future__ import annotations

from typing import Any

from careereng.config.schema import BrowserBudgetsConfig
from careereng.utils import safe_file_stem


class BatchApplyDebugRunner:
    _TERMINAL_APPLY_STATES = {
        "already_applied",
        "submitted",
        "apply_failed",
        "blocked",
        "terminal_filtered_out",
    }

    def __init__(self, job_flow: Any):
        self.job_flow = job_flow

    @property
    def session_preparation_timeout_seconds(self) -> int:
        budgets = getattr(self.job_flow, "browser_budgets", None)
        default = BrowserBudgetsConfig().debug_session_preparation_timeout_seconds
        return int(getattr(budgets, "debug_session_preparation_timeout_seconds", default) or default)

    def _load_batch(self, *, batch_id: str, session_id: str) -> dict[str, Any]:
        requested = str(batch_id or "latest").strip() or "latest"
        job_store = self.job_flow.job_store
        if requested != "latest":
            batch = job_store.load_batch(requested)
            if not batch:
                raise FileNotFoundError(f"job batch not found: {requested}")
            return batch
        rows = job_store.list_batches(session_id=session_id or None, include_terminal=True)
        if not rows and session_id:
            rows = job_store.list_batches(include_terminal=True)
        if not rows:
            raise FileNotFoundError("no job batches found")
        return rows[0]

    def _sample_run_jobs(self, site_key: str, batch_id: str, *, limit: int) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        seen_job_ids: set[str] = set()
        rows = self.job_flow._merged_run_job_rows_for_batch(site_key, batch_id)
        for row in rows:
            job_id = str(row.get("job_id") or "").strip()
            job_url = str(row.get("url") or "").strip()
            if not job_id or not job_url or job_id in seen_job_ids:
                continue
            if self.job_flow._is_apply_row_terminal(row):
                continue
            selected.append(row)
            seen_job_ids.add(job_id)
            if len(selected) >= max(1, int(limit or 1)):
                break
        return selected

    def _resume_waiting_solution_if_materialized(
        self,
        *,
        batch: dict[str, Any],
        site_key: str,
        current: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        flow = self.job_flow
        is_waiting = str(current.get("status") or "") == "waiting_solution" or str(
            (current.get("apply") or {}).get("status") or ""
        ) == "waiting_solution"
        if not is_waiting:
            return batch, current
        proposals = flow._active_run_local_apply_proposals(site_key=site_key, batch_id=str(batch.get("batch_id") or ""))
        if not proposals:
            return batch, current
        apply = dict(current.get("apply") or {})
        loop_control = dict(apply.get("loop_control") or {})
        proposal = proposals[-1]
        proposal_payload = proposal.get("proposal") if isinstance(proposal.get("proposal"), dict) else {}
        loop_control.update(
            {
                "waiting_solution": False,
                "materialized_change": True,
                "proposal_status": str(proposal_payload.get("proposal_status") or "materialized"),
                "active_run_local_proposal_id": str(proposal_payload.get("proposal_id") or ""),
                "active_run_local_proposal_memory_id": str(proposal.get("memory_id") or ""),
            }
        )
        apply.update({"status": "running", "loop_control": loop_control})
        resumed = {
            **current,
            "status": "running",
            "reason_tag": "item_loop_resume_with_run_local_overlay",
            "apply": apply,
            "message": "Resuming item loop with an applied run-local evolution proposal.",
        }
        batch = flow.job_store.update_site(batch, site_key, resumed)
        batch["status"] = flow._compute_batch_status(batch)
        batch = flow.job_store.save_batch(batch)
        return batch, resumed

    def _select_run_job(
        self,
        *,
        site_key: str,
        batch_id: str,
        job_id: str = "",
        title_contains: str = "",
    ) -> dict[str, Any]:
        rows = self.job_flow._run_job_rows(site_key, batch_id)
        normalized_job_id = str(job_id or "").strip()
        normalized_title = str(title_contains or "").strip().lower()
        if normalized_job_id:
            for row in rows:
                if str(row.get("job_id") or "").strip() == normalized_job_id:
                    return row
            raise ValueError(f"job not found in batch {batch_id}: {normalized_job_id}")
        if not normalized_title:
            raise ValueError("job selector missing")
        matches = [
            row
            for row in rows
            if normalized_title in str(row.get("title") or "").strip().lower()
        ]
        if not matches:
            raise ValueError(f"no jobs matched title filter in batch {batch_id}: {title_contains}")
        if len(matches) > 1:
            titles = ", ".join(str(row.get("title") or "") for row in matches[:3])
            raise ValueError(f"multiple jobs matched title filter: {titles}")
        return matches[0]

    @classmethod
    def _debug_site_row(cls, *, source_site_row: dict[str, Any], target_row: dict[str, Any]) -> dict[str, Any]:
        site_key = str(source_site_row.get("site_key") or "")
        site_name = str(source_site_row.get("site_name") or site_key)
        entry_url = str(source_site_row.get("entry_url") or source_site_row.get("current_url") or "").strip()
        if not entry_url:
            entry_url = str(target_row.get("url") or "").strip()
        return {
            "site_key": site_key,
            "site_name": site_name,
            "status": "partial_completed",
            "reason_tag": "ready",
            "entry_url": entry_url,
            "skill_path": str(source_site_row.get("skill_path") or ""),
            "retrieve": {
                "status": "done",
                "count": 1,
            },
            "apply": {
                "status": "pending",
                "attempted": 0,
                "submitted": 0,
                "already_applied": 0,
                "filtered_out": 0,
                "failed": 0,
                "blocked": 0,
            },
            "message": "单岗位调试批次已就绪，等待投递。",
            "current_phase": "job_retrieval",
            "current_url": str(target_row.get("url") or ""),
            "trace_ref": "",
            "step_count": 0,
        }

    @classmethod
    def _debug_run_row(cls, *, source_row: dict[str, Any]) -> dict[str, Any]:
        apply_state = str(source_row.get("apply_state") or "").strip()
        if apply_state.lower() in cls._TERMINAL_APPLY_STATES:
            apply_state = ""
        return {
            "job_id": str(source_row.get("job_id") or ""),
            "canonical_job_id": str(source_row.get("canonical_job_id") or ""),
            "site_id": str(source_row.get("site_id") or ""),
            "employer": str(source_row.get("employer") or ""),
            "title": str(source_row.get("title") or ""),
            "url": str(source_row.get("url") or ""),
            "location": str(source_row.get("location") or ""),
            "posted_at": str(source_row.get("posted_at") or ""),
            "posted_label": str(source_row.get("posted_label") or ""),
            "employment_type": str(source_row.get("employment_type") or ""),
            "match_label": str(source_row.get("match_label") or ""),
            "apply_state": apply_state,
            "description_ref": str(source_row.get("description_ref") or ""),
        }

    def create_debug_batch(
        self,
        *,
        batch_id: str,
        site_key: str,
        session_id: str,
        turn_id: str,
        job_id: str = "",
        title_contains: str = "",
    ) -> str:
        flow = self.job_flow
        source_batch = self._load_batch(batch_id=batch_id, session_id=session_id)
        source_batch_id = str(source_batch.get("batch_id") or "")
        normalized_site_key = safe_file_stem(site_key)
        sites = source_batch.get("sites") if isinstance(source_batch.get("sites"), dict) else {}
        current = sites.get(normalized_site_key)
        if not isinstance(current, dict):
            raise KeyError(f"site not found in batch: {site_key}")
        target_row = self._select_run_job(
            site_key=normalized_site_key,
            batch_id=source_batch_id,
            job_id=job_id,
            title_contains=title_contains,
        )
        title = str(target_row.get("title") or "").strip()
        debug_batch = flow.job_store.create_batch(
            session_id=session_id,
            turn_id=turn_id,
            user_message=f"debug apply isolate {normalized_site_key}: {title or 'job'}",
            apply_requested=True,
            sites=[self._debug_site_row(source_site_row=current, target_row=target_row)],
        )
        debug_batch_id = str(debug_batch.get("batch_id") or "")
        flow.site_tools.site_store.update_run_jobs(
            normalized_site_key,
            [self._debug_run_row(source_row=target_row)],
            session_id,
            turn_id,
            debug_batch_id,
        )
        return debug_batch_id

    def run(
        self,
        *,
        batch_id: str,
        site_key: str,
        limit: int,
        session_id: str,
        turn_id: str,
        apply_only: bool = False,
    ) -> str:
        flow = self.job_flow
        if not flow.browser_runner:
            raise RuntimeError("browser automation is disabled")
        batch = self._load_batch(batch_id=batch_id, session_id=session_id)
        batch_id = str(batch.get("batch_id") or "")
        normalized_site_key = safe_file_stem(site_key)
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        current = sites.get(normalized_site_key)
        if not isinstance(current, dict):
            raise KeyError(f"site not found in batch: {site_key}")
        batch, current = self._resume_waiting_solution_if_materialized(
            batch=batch,
            site_key=normalized_site_key,
            current=current,
        )
        selected_rows = self._sample_run_jobs(normalized_site_key, batch_id, limit=limit)
        if not selected_rows:
            raise ValueError(f"no jobs with URL found for site {normalized_site_key} in batch {batch_id}")

        entry_url = str(current.get("entry_url") or current.get("current_url") or "")
        site_registry_row = flow.site_tools.site_store.find_site(normalized_site_key)
        if not entry_url and isinstance(site_registry_row, dict):
            entry_url = str(site_registry_row.get("base_url") or "")
        if not entry_url:
            entry_url = str(selected_rows[0].get("url") or "")

        try:
            flow.site_tools.ensure_default_resume_pdf()
        except Exception as exc:
            updated = dict(current)
            updated["reason_tag"] = "resume_pdf_unavailable"
            updated["message"] = str(exc)
            updated["apply"] = {
                **dict(current.get("apply") or {}),
                "status": "failed",
                "attempted": 0,
                "submitted": 0,
            }
            batch = flow.job_store.update_site(batch, normalized_site_key, updated)
            batch["status"] = flow._compute_batch_status(batch)
            batch = flow.job_store.save_batch(batch)
            flow._generate_batch_report_if_possible(batch)
            return flow._format_batch_summary(batch)

        login_result: Any | None = None
        if not apply_only:
            login_result = flow.browser_runner.run_site(
                site_key=normalized_site_key,
                site_name=str(current.get("site_name") or normalized_site_key),
                entry_url=entry_url,
                session_id=session_id,
                turn_id=turn_id,
                batch_id=batch_id,
                resume=False,
                phase_slugs=("session_preparation",),
                phase_timeout_seconds_override=self.session_preparation_timeout_seconds,
            )
            if str(getattr(login_result, "status", "") or "") in {"blocked", "failed"}:
                updated = flow._browser_result_to_site_row(
                    result=login_result,
                    existing=current,
                    allow_apply=str((current.get("apply") or {}).get("status") or "") != "skipped",
                )
                batch = flow.job_store.update_site(batch, normalized_site_key, updated)
                batch["status"] = flow._compute_batch_status(batch)
                batch = flow.job_store.save_batch(batch)
                flow._generate_batch_report_if_possible(batch)
                return flow._format_batch_summary(batch)

        last_result: Any | None = login_result
        for target in selected_rows:
            job_id = str(target.get("job_id") or "").strip()
            job_url = str(target.get("url") or "").strip()
            if not job_id or not job_url:
                continue
            flow._mark_apply_job_uses_run_local_proposal(
                site_key=normalized_site_key,
                batch_id=batch_id,
                session_id=session_id,
                turn_id=turn_id,
                job_id=job_id,
            )
            last_result = flow.browser_runner.run_site(
                site_key=normalized_site_key,
                site_name=str(current.get("site_name") or normalized_site_key),
                entry_url=job_url,
                session_id=session_id,
                turn_id=turn_id,
                batch_id=batch_id,
                resume=False,
                phase_slugs=("apply",),
                apply_target_job_ids=(job_id,),
                phase_timeout_seconds_override=flow.APPLY_JOB_PHASE_TIMEOUT_SECONDS,
                timeout_ms_override=flow.APPLY_JOB_TIMEOUT_MS,
            )
            latest_rows = {str(row.get("job_id") or ""): row for row in flow._run_job_rows(normalized_site_key, batch_id)}
            latest_row = latest_rows.get(job_id) or {}
            if flow._is_apply_row_terminal(latest_row):
                continue
            status = "blocked" if str(getattr(last_result, "status", "") or "") == "blocked" else "apply_failed"
            error_text = str(getattr(last_result, "message", "") or "").strip() or "sample apply ended without terminal job update"
            if flow._apply_flow_started_without_terminal(job_url=job_url, last_result=last_result, latest_row=latest_row):
                loop_gap_row = flow._write_unclosed_apply_loop_gap(
                    site_key=normalized_site_key,
                    batch_id=batch_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    job_row={**target, **latest_row},
                    last_result=last_result,
                    error_text=error_text,
                )
                updated = flow._loop_control_pause_site_row(
                    site_key=normalized_site_key,
                    existing=current,
                    batch_id=batch_id,
                    last_result=last_result,
                    job_row=loop_gap_row,
                    turn_id=turn_id,
                )
                if str(updated.get("status") or "") in {"blocked", "waiting_solution"}:
                    batch = flow.job_store.update_site(batch, normalized_site_key, updated)
                    batch["status"] = flow._compute_batch_status(batch)
                    batch = flow.job_store.save_batch(batch)
                    flow._generate_batch_report_if_possible(batch)
                    return flow._format_batch_summary(batch)
                current = updated
                batch = flow.job_store.update_site(batch, normalized_site_key, updated)
                batch["status"] = flow._compute_batch_status(batch)
                batch = flow.job_store.save_batch(batch)
                continue
            flow._seal_apply_row_terminal(
                site_key=normalized_site_key,
                batch_id=batch_id,
                session_id=session_id,
                turn_id=turn_id,
                job_id=job_id,
                status=status,
                error_text=error_text,
            )

        flow._promote_apply_run_to_history(
            site_key=normalized_site_key,
            batch_id=batch_id,
            session_id=session_id,
            turn_id=turn_id,
        )
        updated = flow._finalize_apply_site_row(
            site_key=normalized_site_key,
            existing=current,
            batch_id=batch_id,
            last_result=last_result,
        )
        batch = flow.job_store.update_site(batch, normalized_site_key, updated)
        batch["status"] = flow._compute_batch_status(batch)
        batch = flow.job_store.save_batch(batch)
        flow._generate_batch_report_if_possible(batch)
        return flow._format_batch_summary(batch)
