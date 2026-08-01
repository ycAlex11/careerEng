"""One summary boundary for each completed exploration cycle.

The generic loop records two independent limits: repeated failures within one
cycle stop that cycle, while a separate cycle limit bounds automatic
exploration. Codex authors the evidence-backed Skill/lesson update; this
module only persists objective cycle outcomes and schedules the next cycle.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.evolution.memory_units import EvolutionMemoryStore, run_local_units_for_batch_site
from careereng.evolution.work_items import create_site_exploration_synthesis_card
from careereng.utils import now_iso, read_json, write_json


class SiteRunEvolutionCoordinator:
    """Persist structural site-run progression without judging the site."""

    def __init__(self, job_flow: Any, *, exploration_attempt_limit: int | None = None):
        self.job_flow = job_flow
        self.job_store = job_flow.job_store
        self.loop_engine = job_flow.loop_engine
        default_limit = int(getattr(getattr(job_flow, "browser_budgets", None), "inner_max_failures", 3) or 3)
        self.exploration_attempt_limit = max(1, int(exploration_attempt_limit or default_limit))

    def request_summary_if_needed(
        self,
        batch: dict[str, Any],
        *,
        site_key: str = "",
    ) -> tuple[dict[str, Any], bool]:
        """Create a summary for one finished exploration site without pausing its batch."""

        if not isinstance(batch, dict) or not batch:
            return batch, False
        if str(batch.get("status") or "") == "cancelled":
            return batch, False
        batch_id = str(batch.get("batch_id") or "")
        changed = False
        requested_site_key = str(site_key or "").strip()
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        for candidate_site_key, row in list(sites.items()):
            if requested_site_key and str(candidate_site_key) != requested_site_key:
                continue
            if not self._requires_summary(row):
                continue
            cycle_outcome = self._cycle_outcome(row)
            summary_row = self._archive_legacy_loop_request(row) if cycle_outcome == "repeated_failure_threshold" else row
            card = create_site_exploration_synthesis_card(
                workspace=Path(self.job_store.workspace),
                project_root=Path(self.job_flow.project_root),
                site_key=str(candidate_site_key),
                site_name=str(summary_row.get("site_name") or candidate_site_key),
                batch_id=batch_id,
                skill_path=str(summary_row.get("skill_path") or ""),
                cycle_outcome=cycle_outcome,
            )
            request = self.loop_engine.create_loop_control_solution_request(
                artifacts={"action_card_id": str(card.get("card_id") or "")},
                context_overrides={
                    "solution_level": "site_run_summary",
                    "solution_request_kind": "site_run_summary",
                    "exploration_completion": True,
                    "site_key": str(candidate_site_key),
                    "batch_id": batch_id,
                },
            )
            if str(request.get("error") or "").strip():
                raise RuntimeError(str(request.get("error") or "failed to create site-run summary request"))
            if not str(request.get("solution_request") or "").strip():
                continue
            updated = self._summary_requested_row(
                summary_row,
                request,
                cycle_outcome=cycle_outcome,
            )
            batch = self._replace_site_row(batch, str(candidate_site_key), updated)
            changed = True
        if changed:
            # This summary belongs to one site. Batch status remains a pure
            # run-group projection so other sites keep running.
            batch["status"] = self._batch_status_after_summary(batch)
            batch = self.job_store.save_batch(batch)
        return batch, changed

    def consume_applied_summary(self, *, batch: dict[str, Any], site_key: str, run_id: str) -> tuple[dict[str, Any], str]:
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        row = sites.get(site_key) if isinstance(sites.get(site_key), dict) else {}
        summary = self._summary_from_row(row)
        if str(summary.get("run_id") or "") != str(run_id or ""):
            raise ValueError("site-run summary does not belong to this site batch")
        run_payload = read_json(Path(self.job_store.workspace) / "evolution" / "runs" / run_id / "run.json")
        if str(run_payload.get("status") or "") != "applied":
            raise ValueError("site-run summary proposal has not been applied")
        decision = self._site_mode_decision(site_key=site_key, run_id=run_id)
        self._close_run_local_scope(batch_id=str(batch.get("batch_id") or ""), site_key=site_key, run_id=run_id)
        evolution = dict(row.get("evolution") or {})
        loop_control = dict(evolution.get("loop_control") or {})
        loop_control.update(
            {
                "synthesis_required": False,
                "site_run_summary": {
                    **summary,
                    "consumed": True,
                    "decision": decision,
                },
            }
        )
        evolution["loop_control"] = loop_control
        evolution["status"] = "completed"
        updated = {
            **row,
            "evolution": evolution,
            "solution_run_id": "",
            "solution_request": "",
            "proposal_output_path": "",
        }
        batch = self.job_store.update_site(batch, site_key, updated)
        batch["status"] = self._batch_status_after_summary(batch)
        batch = self.job_store.save_batch(batch)
        return batch, decision

    def retain_pending_summary(self, batch: dict[str, Any]) -> dict[str, Any]:
        """Restore a pending site summary without converting the whole batch to a wait."""

        if not isinstance(batch, dict) or str(batch.get("status") or "") == "cancelled":
            return batch
        if not self._has_pending_summary(batch):
            return batch
        updated = dict(batch)
        sites = updated.get("sites") if isinstance(updated.get("sites"), dict) else {}
        for site_key, row in list(sites.items()):
            if not isinstance(row, dict) or not self._summary_from_row(row).get("run_id"):
                continue
            evolution = dict(row.get("evolution") or {})
            if str(evolution.get("status") or "") == "summary_pending":
                continue
            evolution["status"] = "summary_pending"
            updated = self._replace_site_row(updated, str(site_key), {**row, "evolution": evolution})
        updated["status"] = self._batch_status_after_summary(updated)
        return self.job_store.save_batch(updated)

    def create_followup_if_needed(self, *, batch: dict[str, Any], site_key: str, decision: str) -> dict[str, Any]:
        """Requeue one site only for an unresolved exploration cycle.

        The applied proposal supplies the Skill/lesson update and site mode.
        Whether the current cycle needs another validation pass comes only
        from persisted execution facts, never from a proposal follow-up flag.
        The continuation stays in the same batch run group.
        """

        outcome = self._summary_cycle_outcome(batch=batch, site_key=site_key)
        if outcome not in {"repeated_failure_threshold", "unresolved_apply_targets"}:
            self._record_auto_followup_stopped(
                batch=batch,
                site_key=site_key,
                reason=outcome or "cycle_completed",
            )
            return {}
        if decision != "exploration":
            self._record_auto_followup_stopped(
                batch=batch,
                site_key=site_key,
                reason="summary_mode_not_exploration",
            )
            return {}
        row = self._site_row(batch=batch, site_key=site_key)
        site_run = self._site_run(row, batch=batch)
        attempt = max(1, int(site_run.get("attempt") or 1))
        max_attempts = self.exploration_attempt_limit
        if attempt >= max_attempts:
            # The worker has completed its bounded autonomous exploration.
            # Keep its applied summary/evidence durable for the Desktop UI,
            # but do not silently start a fourth attempt or turn this into a
            # user-wait browser state.
            self._record_auto_followup_stopped(
                batch=batch,
                site_key=site_key,
                reason="exploration_attempt_limit",
            )
            return {}
        # The next exploration attempt is another work item for this site in
        # the same run group. Its retained Codex thread is reused by the
        # session store; no synthetic follow-up batch is created.
        root_batch_id = str(site_run.get("root_batch_id") or batch.get("batch_id") or "")
        evolution = dict(row.get("evolution") or {})
        evolution["site_run"] = {
            **site_run,
            "root_batch_id": root_batch_id,
            "attempt": attempt + 1,
            "max_attempts": max_attempts,
            "reason": "applied_site_run_summary_requested_exploration",
        }
        next_row = {
            **row,
            "status": "queued",
            "reason_tag": "site_exploration_followup",
            "message": "Continuing this site's exploration after its summary.",
            "current_phase": "",
            "evolution": evolution,
            "solution_run_id": "",
            "solution_request": "",
            "proposal_output_path": "",
        }
        batch = self.job_store.update_site(batch, site_key, next_row)
        batch["status"] = "running"
        batch = self.job_store.save_batch(batch)
        return {"batch_id": str(batch.get("batch_id") or ""), "site_key": site_key, "same_batch": True}

    def normalize_legacy_waiting_summary(self, batch: dict[str, Any]) -> dict[str, Any]:
        """Archive one old blocking synthesis request without deleting evidence.

        This is a mechanical migration for records created by the former
        outer-loop handoff.  It never invents a proposal, readiness decision,
        or job outcome.
        """

        legacy_batch = str(batch.get("status") or "") == "waiting_solution" or str(batch.get("reason_tag") or "") == "legacy_site_run_summary_archived"
        if not legacy_batch:
            return batch
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        changed = False
        for site_key, row in list(sites.items()):
            if not isinstance(row, dict):
                continue
            apply = dict(row.get("apply") or {})
            control = dict(apply.get("loop_control") or {})
            is_waiting = str(row.get("status") or "") == "waiting_solution"
            is_archived = str(row.get("reason_tag") or "") == "legacy_site_run_summary_archived"
            if not is_archived and (not is_waiting or str(control.get("solution_request_kind") or "") != "synthesis_work_order"):
                continue
            prior_legacy = apply.get("legacy_site_run_summary") if isinstance(apply.get("legacy_site_run_summary"), dict) else {}
            if not prior_legacy:
                prior_legacy = row.get("legacy_site_run_summary") if isinstance(row.get("legacy_site_run_summary"), dict) else {}
            legacy_summary = {
                "run_id": str(control.get("solution_run_id") or row.get("solution_run_id") or prior_legacy.get("run_id") or ""),
                "solution_request": str(control.get("solution_request") or row.get("solution_request") or prior_legacy.get("solution_request") or ""),
                "proposal_output_path": str(control.get("proposal_output_path") or row.get("proposal_output_path") or prior_legacy.get("proposal_output_path") or ""),
                "evidence_pack": str(control.get("evidence_pack") or prior_legacy.get("evidence_pack") or ""),
                "status": "archived_unapplied_legacy_request",
            }
            run_id = legacy_summary["run_id"]
            if run_id:
                run_path = Path(self.job_store.workspace) / "evolution" / "runs" / run_id / "run.json"
                run = read_json(run_path)
                if str(run.get("status") or "") == "waiting_solution":
                    lifecycle = list(run.get("lifecycle") or [])
                    lifecycle.append(
                        {
                            "status": "cancelled",
                            "at": now_iso(),
                            "summary": "Archived an unapplied legacy outer-loop request during site-run migration.",
                        }
                    )
                    run.update({"status": "cancelled", "updated_at": now_iso(), "lifecycle": lifecycle})
                    write_json(run_path, run)
            apply["status"] = "completed"
            apply["legacy_site_run_summary"] = legacy_summary
            apply.pop("loop_control", None)
            updated = {
                **row,
                "status": "completed",
                "reason_tag": "legacy_site_run_summary_archived",
                "message": "Archived the unapplied legacy summary request; execution evidence remains available.",
                "apply": apply,
                "legacy_site_run_summary": legacy_summary,
            }
            for key in ("solution_run_id", "solution_request", "proposal_output_path"):
                updated.pop(key, None)
            batch = self._replace_site_row(batch, str(site_key), updated)
            changed = True
        if changed:
            batch["status"] = "completed"
            batch["reason_tag"] = "legacy_site_run_summary_archived"
            batch = self.job_store.save_batch(batch)
        return batch

    def _replace_site_row(self, batch: dict[str, Any], site_key: str, row: dict[str, Any]) -> dict[str, Any]:
        """Replace one legacy row when migration must remove obsolete keys."""

        updated = dict(batch)
        sites = dict(updated.get("sites") or {})
        sites[site_key] = dict(row)
        updated["sites"] = sites
        return self.job_store.save_batch(updated)

    def _archive_legacy_loop_request(self, row: dict[str, Any]) -> dict[str, Any]:
        """Preserve legacy threshold evidence without retaining its old request."""

        apply = dict(row.get("apply") or {})
        control = apply.get("loop_control") if isinstance(apply.get("loop_control"), dict) else {}
        if not control:
            return row
        legacy_run_id = str(control.get("solution_run_id") or "").strip()
        if legacy_run_id:
            run_path = Path(self.job_store.workspace) / "evolution" / "runs" / legacy_run_id / "run.json"
            run = read_json(run_path)
            if str(run.get("status") or "") in {"waiting_solution", "proposal_written"}:
                lifecycle = list(run.get("lifecycle") or [])
                lifecycle.append(
                    {
                        "status": "cancelled",
                        "at": now_iso(),
                        "summary": "Superseded by the unified exploration-cycle synthesis request.",
                    }
                )
                run.update({"status": "cancelled", "updated_at": now_iso(), "lifecycle": lifecycle})
                write_json(run_path, run)
        apply["legacy_loop_control"] = control
        apply.pop("loop_control", None)
        return {
            **row,
            "apply": apply,
            "legacy_loop_control": {
                "solution_run_id": legacy_run_id,
                "failure_pattern": str(control.get("failure_pattern") or ""),
                "attempts": int(control.get("attempts") or 0),
            },
        }

    @classmethod
    def _requires_summary(cls, row: Any) -> bool:
        if not isinstance(row, dict):
            return False
        status = str(row.get("status") or "")
        if status not in {"completed", "partial_completed", "failed", "blocked", "waiting_solution"}:
            return False
        scope = row.get("evolution_scope") if isinstance(row.get("evolution_scope"), dict) else {}
        if str(scope.get("execution_mode") or row.get("execution_mode") or row.get("site_mode") or "") != "exploration":
            return False
        if not bool(scope.get("active", True)):
            return False
        if status in {"blocked", "waiting_solution"} and cls._cycle_outcome(row) != "repeated_failure_threshold":
            return False
        return not bool(SiteRunEvolutionCoordinator._summary_from_row(row).get("run_id"))

    @staticmethod
    def _summary_from_row(row: dict[str, Any]) -> dict[str, Any]:
        evolution = row.get("evolution") if isinstance(row.get("evolution"), dict) else {}
        control = evolution.get("loop_control") if isinstance(evolution.get("loop_control"), dict) else {}
        return dict(control.get("site_run_summary") or {})

    @classmethod
    def _has_pending_summary(cls, batch: dict[str, Any]) -> bool:
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        return any(
            isinstance(row, dict)
            and bool(cls._summary_from_row(row).get("run_id"))
            and not bool(cls._summary_from_row(row).get("consumed"))
            for row in sites.values()
        )

    def _batch_status_after_summary(self, batch: dict[str, Any]) -> str:
        compute = getattr(self.job_flow, "_compute_batch_status", None)
        if callable(compute):
            return str(compute(batch) or "completed")
        return "completed"

    @staticmethod
    def _site_row(*, batch: dict[str, Any], site_key: str) -> dict[str, Any]:
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        row = sites.get(site_key) if isinstance(sites.get(site_key), dict) else {}
        return dict(row)

    @staticmethod
    def _site_run(row: dict[str, Any], *, batch: dict[str, Any] | None = None) -> dict[str, Any]:
        evolution = row.get("evolution") if isinstance(row.get("evolution"), dict) else {}
        site_run = evolution.get("site_run") if isinstance(evolution.get("site_run"), dict) else {}
        if site_run:
            return dict(site_run)
        # Compatibility with records created before site-scoped loop state.
        legacy = (batch or {}).get("site_run") if isinstance((batch or {}).get("site_run"), dict) else {}
        return dict(legacy)

    @staticmethod
    def _summary_requested_row(
        row: dict[str, Any],
        request: dict[str, Any],
        *,
        cycle_outcome: str,
    ) -> dict[str, Any]:
        evolution = dict(row.get("evolution") or {})
        loop_control = dict(evolution.get("loop_control") or {})
        summary = {
            "run_id": str(request.get("run_id") or ""),
            "solution_request": str(request.get("solution_request") or ""),
            "proposal_output_path": str(request.get("proposal_output_path") or ""),
            "evidence_pack": str(request.get("evidence_pack") or ""),
            "cycle_outcome": str(cycle_outcome or ""),
        }
        loop_control.update({"synthesis_required": True, "site_run_summary": summary})
        evolution["loop_control"] = loop_control
        evolution["status"] = "summary_pending"
        return {
            **row,
            "evolution": evolution,
            "solution_run_id": summary["run_id"],
            "solution_request": summary["solution_request"],
            "proposal_output_path": summary["proposal_output_path"],
        }

    def _site_mode_decision(self, *, site_key: str, run_id: str) -> str:
        applied = read_json(Path(self.job_store.workspace) / "evolution" / "runs" / run_id / "applied_files.json")
        for row in applied.get("files") if isinstance(applied.get("files"), list) else []:
            if not isinstance(row, dict) or str(row.get("change_type") or "") != "site_mode_update":
                continue
            if str(row.get("site_key") or "") != str(site_key):
                continue
            mode = str(row.get("mode") or "").lower().strip()
            if mode in {"ready", "exploration"}:
                return mode
        return ""

    @staticmethod
    def _summary_cycle_outcome(*, batch: dict[str, Any], site_key: str) -> str:
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        row = sites.get(site_key) if isinstance(sites.get(site_key), dict) else {}
        summary = SiteRunEvolutionCoordinator._summary_from_row(row)
        persisted = str(summary.get("cycle_outcome") or "").strip()
        return persisted or SiteRunEvolutionCoordinator._cycle_outcome(row)

    @staticmethod
    def _cycle_outcome(row: dict[str, Any]) -> str:
        """Classify persisted execution facts without interpreting site policy."""

        apply = row.get("apply") if isinstance(row.get("apply"), dict) else {}
        control = apply.get("loop_control") if isinstance(apply.get("loop_control"), dict) else {}
        transition = control.get("item_loop_transition") if isinstance(control.get("item_loop_transition"), dict) else {}
        if (
            str(transition.get("action") or "") == "pause_threshold"
            or str(transition.get("reason_tag") or "") == "item_loop_refinement_threshold"
        ):
            return "repeated_failure_threshold"
        if int(apply.get("submitted") or 0) > 0 and not int(apply.get("failed") or 0) and not int(apply.get("blocked") or 0):
            return "successful_apply_cycle"
        if int(apply.get("failed") or 0) or int(apply.get("blocked") or 0):
            return "unresolved_apply_targets"
        return "no_eligible_apply_target"

    def _record_auto_followup_stopped(self, *, batch: dict[str, Any], site_key: str, reason: str) -> None:
        row = self._site_row(batch=batch, site_key=site_key)
        site_run = self._site_run(row, batch=batch)
        attempt = max(1, int(site_run.get("attempt") or 1))
        max_attempts = self.exploration_attempt_limit
        evolution = dict(row.get("evolution") or {})
        evolution["site_run"] = {
            **site_run,
            "attempt": attempt,
            "max_attempts": max_attempts,
            "auto_followup_stopped": True,
            "stop_reason": reason,
        }
        self.job_store.save_batch(self.job_store.update_site(batch, site_key, {**row, "evolution": evolution}))
        append_event = getattr(self.job_store, "append_event", None)
        if callable(append_event):
            append_event(
                "evolution.exploration.user_direction_required",
                {
                    "batch_id": str(batch.get("batch_id") or ""),
                    "site_key": site_key,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "reason": reason,
                },
            )

    def _close_run_local_scope(self, *, batch_id: str, site_key: str, run_id: str) -> None:
        store = EvolutionMemoryStore(Path(self.job_store.workspace))
        units = run_local_units_for_batch_site(
            workspace=Path(self.job_store.workspace),
            site_key=site_key,
            batch_id=batch_id,
            statuses=["active"],
        )
        for scope in sorted({str(unit.get("scope") or "") for unit in units if str(unit.get("scope") or "")}):
            store.close_run_local_scope_after_synthesis(
                scope=scope,
                reason="site-run summary consumed run-local evidence",
                run_id=run_id,
            )
