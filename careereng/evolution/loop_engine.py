"""Generic loop-engine orchestration for workflow evolution.

This module owns reusable evolution-loop mechanics. It does not encode
site-specific workflow, form-filling, browser selector, or matching policy.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from careereng.browser_context import WorkflowMemoryStore
from careereng.evolution.browser_control.lessons import BrowserControlLessonStore, render_lessons_markdown
from careereng.evolution.item_loop import plan_item_loop_transition
from careereng.evolution.loop_control import (
    EVOLUTION_DECISION_CONTINUE,
    EVOLUTION_DECISION_NEEDS_SOLUTION,
    LOOP_ACTION_TRIGGER_REFINEMENT,
    build_evolution_decision,
    create_loop_control_artifacts,
    loop_control_from_row,
    loop_control_is_human_only_gap,
)
from careereng.evolution.memory_units import (
    EvolutionMemoryStore,
    build_loop_evolution_memory,
    evolution_memory_has_materialized_change,
)
from careereng.evolution.solution_provider import EvolutionSolutionError, create_solution_request_for_action_card
from careereng.utils import make_id


class ApplyLoopEngine:
    """Reusable engine for apply-item loop evolution.

    JobFlow owns the browser/apply-list orchestration. This engine owns the
    generic evolution contract around loop-control evidence, proposals, usage,
    validation, and outer-loop follow-up packaging.
    """

    def __init__(
        self,
        *,
        project_root: Path,
        workspace: Path,
        site_store: Any,
        job_store: Any,
        browser_budgets: Any,
        trace_path_for_ref: Callable[[Any], Path | None],
        run_job_rows: Callable[[str, str], list[dict[str, Any]]],
        merged_run_job_rows: Callable[[str, str], list[dict[str, Any]]],
        apply_counters_from_run: Callable[[str, str], dict[str, int]],
        apply_counter_payload: Callable[[dict[str, int]], dict[str, int]],
    ) -> None:
        self.project_root = project_root
        self.workspace = workspace
        self.site_store = site_store
        self.job_store = job_store
        self.browser_budgets = browser_budgets
        self.trace_path_for_ref = trace_path_for_ref
        self.run_job_rows = run_job_rows
        self.merged_run_job_rows = merged_run_job_rows
        self.apply_counters_from_run = apply_counters_from_run
        self.apply_counter_payload = apply_counter_payload

    @property
    def refinement_attempts_per_batch(self) -> int:
        return int(
            getattr(
                self.browser_budgets,
                "inner_max_failures",
                getattr(self.browser_budgets, "loop_control_refinement_attempts_per_batch", 3),
            )
            or 3
        )

    @property
    def user_input_attempts_per_batch(self) -> int:
        return int(getattr(self.browser_budgets, "loop_control_user_input_attempts_per_batch", 3) or 3)

    @property
    def outer_batch_attempts(self) -> int:
        return int(
            getattr(
                self.browser_budgets,
                "outer_max_attempts",
                getattr(self.browser_budgets, "loop_control_outer_batch_attempts", 3),
            )
            or 3
        )

    @property
    def failed_batches_per_pattern(self) -> int:
        return int(self.browser_budgets.loop_control_failed_batches_per_pattern)

    def aggregate_apply_status_for_run(self, *, site_key: str, batch_id: str) -> str:
        rows = self.merged_run_job_rows(site_key, batch_id)
        has_failed = False
        has_blocked = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            application_status = str(row.get("application_status") or "").strip().lower()
            control = loop_control_from_row(row)
            if control:
                if loop_control_is_human_only_gap(control):
                    has_blocked = True
                    continue
                attempts = self.loop_control_pattern_attempts_in_batch(
                    site_key=site_key,
                    batch_id=batch_id,
                    phase="apply",
                    pattern=str(control.get("failure_pattern") or ""),
                )
                if attempts >= self.refinement_attempts_per_batch:
                    has_blocked = True
                continue
            if application_status == "apply_failed":
                has_failed = True
            elif application_status == "blocked":
                has_blocked = True
        if has_failed:
            return "failed"
        if has_blocked:
            return "blocked"
        return "done"

    def active_run_local_apply_proposals(self, *, site_key: str, batch_id: str, limit: int = 3) -> list[dict[str, Any]]:
        try:
            proposals = EvolutionMemoryStore(self.workspace).query(
                scopes=[f"batch:{batch_id}:site:{site_key}:apply"],
                phase="apply",
                lifecycles=["run_local"],
                statuses=["active"],
                limit=max(20, int(limit or 1) * 5),
            )
            materialized = [proposal for proposal in proposals if evolution_memory_has_materialized_change(proposal)]
            materialized.sort(key=lambda row: (str(row.get("created_at") or ""), str(row.get("memory_id") or "")))
            return materialized[-max(1, int(limit or 1)) :]
        except Exception:
            return []

    def mark_apply_job_uses_run_local_proposal(
        self,
        *,
        site_key: str,
        batch_id: str,
        session_id: str,
        turn_id: str,
        job_id: str,
    ) -> dict[str, str]:
        proposals = self.active_run_local_apply_proposals(site_key=site_key, batch_id=batch_id, limit=1)
        if not proposals:
            return {}
        proposal = proposals[-1]
        proposal_payload = proposal.get("proposal") if isinstance(proposal.get("proposal"), dict) else {}
        proposal_id = str(proposal_payload.get("proposal_id") or proposal.get("memory_id") or "").strip()
        memory_id = str(proposal.get("memory_id") or "").strip()
        if not proposal_id and not memory_id:
            return {}
        update = {
            "job_id": job_id,
            "active_run_local_proposal_id": proposal_id,
            "active_run_local_proposal_memory_id": memory_id,
            "active_run_local_proposal_pattern": str(proposal.get("pattern") or ""),
            "active_run_local_proposal_source": str((proposal.get("source") or {}).get("job_id") or ""),
        }
        try:
            self.site_store.update_run_jobs(site_key, [update], session_id, turn_id, batch_id)
        except Exception:
            pass
        event = {
            "batch_id": batch_id,
            "site_key": site_key,
            "phase": "apply",
            "job_id": job_id,
            "proposal_id": proposal_id,
            "pattern": str(proposal.get("pattern") or ""),
        }
        recorded = False
        try:
            EvolutionMemoryStore(self.workspace).append_usage_event(
                memory_id=memory_id,
                proposal_id=proposal_id,
                event=event,
            )
            recorded = True
        except Exception:
            recorded = False
        try:
            self.job_store.append_event(
                "evolution.run_local_proposal.used",
                {
                    **event,
                    "proposal_id": proposal_id,
                    "memory_id": memory_id,
                    "usage_recorded": recorded,
                },
            )
        except Exception:
            pass
        return update

    def record_run_local_proposal_validation(
        self,
        *,
        site_key: str,
        batch_id: str,
        job_row: dict[str, Any],
    ) -> None:
        job_id = str(job_row.get("job_id") or "").strip()
        memory_id = str(job_row.get("active_run_local_proposal_memory_id") or "").strip()
        proposal_id = str(job_row.get("active_run_local_proposal_id") or "").strip()
        pattern = str(job_row.get("active_run_local_proposal_pattern") or "").strip()
        if not memory_id and not proposal_id and job_id:
            for proposal in self.active_run_local_apply_proposals(site_key=site_key, batch_id=batch_id, limit=10):
                proposal_payload = proposal.get("proposal") if isinstance(proposal.get("proposal"), dict) else {}
                for event in proposal.get("usage_events") if isinstance(proposal.get("usage_events"), list) else []:
                    if str(event.get("job_id") or "").strip() != job_id:
                        continue
                    memory_id = str(proposal.get("memory_id") or "").strip()
                    proposal_id = str(proposal_payload.get("proposal_id") or memory_id).strip()
                    pattern = str(proposal.get("pattern") or event.get("pattern") or "").strip()
                    break
                if memory_id or proposal_id:
                    break
        if not memory_id and not proposal_id:
            return
        result = self._run_local_proposal_validation_result(row=job_row, proposal_pattern=pattern)
        event = {
            "batch_id": batch_id,
            "site_key": site_key,
            "phase": "apply",
            "job_id": job_id,
            "title": str(job_row.get("title") or ""),
            "application_status": str(job_row.get("application_status") or ""),
            "decision_status": str(job_row.get("decision_status") or ""),
            "apply_state": str(job_row.get("apply_state") or ""),
            "failure_pattern": str(job_row.get("failure_pattern") or ""),
            "loop_control_action": str(job_row.get("loop_control_action") or job_row.get("recommended_action") or ""),
            "result": result,
        }
        try:
            updated = EvolutionMemoryStore(self.workspace).append_validation_event(
                memory_id=memory_id,
                proposal_id=proposal_id,
                event=event,
            )
            self.job_store.append_event(
                "evolution.run_local_proposal.validated",
                {
                    **event,
                    "proposal_id": proposal_id,
                    "memory_id": memory_id or str(updated.get("memory_id") or ""),
                },
            )
        except Exception:
            return

    def record_loop_control(
        self,
        *,
        site_key: str,
        existing: dict[str, Any],
        batch_id: str,
        last_result: Any | None,
        job_row: dict[str, Any],
        turn_id: str = "",
    ) -> dict[str, Any]:
        control = loop_control_from_row(job_row)
        action = str(control.get("action") or "")
        pattern = str(control.get("failure_pattern") or "unknown_loop_pattern")
        attempts = self.loop_control_pattern_attempts_in_batch(
            site_key=site_key,
            batch_id=batch_id,
            phase="apply",
            pattern=pattern,
        )
        trace_context = self.loop_recent_trace_context(
            trace_ref=getattr(last_result, "trace_ref", "") or existing.get("trace_ref") or "",
            phase="apply",
            limit=8,
        )
        next_iteration_guidance = self.loop_next_iteration_guidance(
            control=control,
            trace_context=trace_context,
            job_row=job_row,
        )
        accepted_lessons_summary = self.related_accepted_lessons_summary(site_key=site_key, phase="apply", limit=5)
        enriched_job_row = {
            **job_row,
            "_loop_recent_tool_chain": trace_context.get("tool_chain") or [],
            "_loop_last_tool_outputs": trace_context.get("outputs") or [],
            "_loop_next_iteration_guidance": next_iteration_guidance,
        }
        artifacts = create_loop_control_artifacts(
            workspace=self.workspace,
            project_root=self.project_root,
            site_key=site_key,
            site_name=str(existing.get("site_name") or site_key),
            phase="apply",
            batch_id=batch_id,
            job_row=enriched_job_row,
            per_batch_attempts=attempts,
            max_refinement_attempts_per_batch=self.refinement_attempts_per_batch,
            max_failed_batches_per_pattern=self.failed_batches_per_pattern,
        )
        decision = build_evolution_decision(
            site_key=site_key,
            phase="apply",
            batch_id=batch_id,
            control=control,
            artifacts=artifacts,
            attempt=1,
            max_attempts=max(1, int(self.outer_batch_attempts or 1)),
        )
        memory = self.persist_loop_control_guidance(
            site_key=site_key,
            batch_id=batch_id,
            control=control,
            job_row=enriched_job_row,
            artifacts=artifacts,
            trace_context=trace_context,
            next_iteration_guidance=next_iteration_guidance,
            accepted_lessons_summary=accepted_lessons_summary,
        )
        materialized = evolution_memory_has_materialized_change(memory)
        transition = plan_item_loop_transition(
            control,
            attempts=attempts,
            max_refinement_attempts=self.refinement_attempts_per_batch,
            max_user_input_attempts=self.user_input_attempts_per_batch,
            has_materialized_change=materialized,
            artifacts=artifacts,
        )
        solution_request = {}
        if bool(transition.should_create_solution_request):
            solution_request = self.create_loop_control_solution_request(artifacts=artifacts)
        if action == LOOP_ACTION_TRIGGER_REFINEMENT and bool(transition.requires_materialized_change) and decision:
            decision = {
                **decision,
                "verdict": EVOLUTION_DECISION_NEEDS_SOLUTION,
                "requires_solution_provider": True,
                "proposal_status": str((memory.get("proposal") or {}).get("proposal_status") or "incomplete"),
                "materialized_change": False,
                "solution_run_id": str(solution_request.get("run_id") or ""),
                "solution_request": str(solution_request.get("solution_request") or ""),
                "proposal_output_path": str(solution_request.get("proposal_output_path") or ""),
                "solution_request_error": str(solution_request.get("error") or ""),
                "next_batch_strategy": "pause_for_solution_provider",
                "validation_plan": (
                    "Do not start a follow-up job or batch until an assistant solution provider writes a concrete "
                    "run_local_overlay, skill_patch, routing_example_append, memory_unit_append, or "
                    "assistant_context_update proposal."
                ),
            }
        elif action == LOOP_ACTION_TRIGGER_REFINEMENT and materialized and decision:
            decision = {
                **decision,
                "requires_solution_provider": False,
                "proposal_status": str((memory.get("proposal") or {}).get("proposal_status") or "materialized"),
                "materialized_change": True,
                "next_batch_strategy": "continue_current_batch_with_run_local_overlay",
                "validation_plan": (
                    "Continue the current item loop. The next apply target should consume the materialized "
                    "run-local overlay and record usage/validation against the proposal."
                ),
            }
        should_pause = bool(transition.pause_loop)
        self.persist_loop_control_workflow_memory(
            site_key=site_key,
            batch_id=batch_id,
            turn_id=turn_id,
            control=control,
            artifacts=artifacts,
        )
        waiting_solution = bool(
            should_pause
            and transition.requires_materialized_change
            and str(solution_request.get("solution_request") or "").strip()
        )
        self.job_store.append_event(
            "loop_control.waiting_solution"
            if waiting_solution
            else ("loop_control.pause" if should_pause else "loop_control.recorded"),
            {
                "batch_id": batch_id,
                "site_key": site_key,
                "phase": "apply",
                "job_id": str(job_row.get("job_id") or ""),
                "loop_control_action": action,
                "failure_pattern": pattern,
                "block_reason_type": str(control.get("block_reason_type") or ""),
                "should_pause": should_pause,
                "proposal_status": str((memory.get("proposal") or {}).get("proposal_status") or ""),
                "materialized_change": bool(materialized),
                "attempts": attempts,
                "waiting_solution": waiting_solution,
                "artifacts": artifacts,
                "solution_request": solution_request,
                "evolution_decision": decision,
                "item_loop_transition": transition.as_dict(),
            },
        )
        return self._loop_control_site_row(
            existing=existing,
            site_key=site_key,
            batch_id=batch_id,
            last_result=last_result,
            control=control,
            action=action,
            pattern=pattern,
            artifacts=artifacts,
            memory=memory,
            materialized=materialized,
            solution_request=solution_request,
            decision=decision,
            transition=transition,
            should_pause=should_pause,
        )

    @staticmethod
    def loop_control_payload_from_site_row(row: dict[str, Any]) -> dict[str, Any]:
        apply_payload = row.get("apply") if isinstance(row.get("apply"), dict) else {}
        loop_payload = apply_payload.get("loop_control") if isinstance(apply_payload.get("loop_control"), dict) else {}
        if not loop_payload:
            return {}
        artifacts = loop_payload.get("artifacts") if isinstance(loop_payload.get("artifacts"), dict) else {}
        return {
            "loop_control_action": str(loop_payload.get("action") or ""),
            "failure_pattern": str(loop_payload.get("failure_pattern") or ""),
            "block_reason_type": str(loop_payload.get("block_reason_type") or ""),
            "gap_type": str(loop_payload.get("gap_type") or artifacts.get("gap_type") or ""),
            "recommended_target": str(
                loop_payload.get("recommended_target")
                or artifacts.get("recommended_target")
                or artifacts.get("target_ref")
                or ""
            ),
            "target": str(loop_payload.get("target") or artifacts.get("target") or artifacts.get("target_ref") or ""),
            "resume_policy": str(loop_payload.get("resume_policy") or artifacts.get("resume_policy") or ""),
            "current_item_ref": str(loop_payload.get("current_item_ref") or row.get("current_url") or row.get("entry_url") or ""),
            "evidence": str(loop_payload.get("evidence") or artifacts.get("evidence") or row.get("message") or ""),
            "refinement_hint": str(loop_payload.get("refinement_hint") or artifacts.get("refinement_hint") or ""),
        }

    def outer_loop_retry_sites(
        self,
        *,
        batch: dict[str, Any],
        operation_job_search: str,
        normalize_operation: Callable[[str], str],
    ) -> list[dict[str, Any]]:
        if normalize_operation(str(batch.get("operation") or "")) != operation_job_search:
            return []
        if not bool(batch.get("apply_requested")):
            return []
        if str(batch.get("status") or "") == "running":
            return []
        batch_id = str(batch.get("batch_id") or "")
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        retry_sites: list[dict[str, Any]] = []
        for site_key, row in sites.items():
            if not isinstance(row, dict):
                continue
            control = loop_control_from_row(self.loop_control_payload_from_site_row(row))
            if not control:
                continue
            if loop_control_is_human_only_gap(control):
                return []
            apply_payload = row.get("apply") if isinstance(row.get("apply"), dict) else {}
            loop_payload = apply_payload.get("loop_control") if isinstance(apply_payload.get("loop_control"), dict) else {}
            if not bool(loop_payload.get("materialized_change")):
                continue
            apply_status = str(apply_payload.get("status") or "").strip()
            site_status = str(row.get("status") or "").strip()
            if not bool(loop_payload.get("should_pause")) and site_status not in {"blocked", "waiting_solution"} and apply_status not in {
                "blocked",
                "waiting_solution",
            }:
                continue
            artifacts = loop_payload.get("artifacts") if isinstance(loop_payload.get("artifacts"), dict) else {}
            decision = build_evolution_decision(
                site_key=str(site_key),
                phase="apply",
                batch_id=batch_id,
                control=control,
                artifacts=artifacts,
                attempt=self.outer_loop_attempt(batch) + 1,
                max_attempts=max(1, int(self.outer_batch_attempts or 1)),
            )
            if str(control.get("action") or "") == LOOP_ACTION_TRIGGER_REFINEMENT:
                decision = {
                    **decision,
                    "verdict": EVOLUTION_DECISION_CONTINUE,
                    "requires_solution_provider": False,
                    "proposal_status": str(loop_payload.get("proposal_status") or "materialized"),
                    "materialized_change": True,
                    "next_batch_strategy": "continue_followup_batch_with_applied_solution",
                    "validation_plan": (
                        "Start the next outer-loop batch with the applied proposal or durable change. "
                        "The next batch should validate whether the same failure pattern is reduced, "
                        "changes to a more specific blocker, or reaches terminal job states."
                    ),
                }
            if bool(decision.get("needs_user_input")):
                return []
            if str(decision.get("verdict") or "") != EVOLUTION_DECISION_CONTINUE:
                continue
            retry_sites.append(
                {
                    "site_key": str(site_key),
                    "row": row,
                    "control": control,
                    "loop_payload": loop_payload,
                    "decision": decision,
                }
            )
        return retry_sites

    @staticmethod
    def outer_loop_attempt(batch: dict[str, Any]) -> int:
        payload = batch.get("evolution_loop") if isinstance(batch.get("evolution_loop"), dict) else {}
        try:
            return max(1, int(payload.get("attempt") or 1))
        except (TypeError, ValueError):
            return 1

    def create_outer_loop_followup_batch(
        self,
        *,
        previous_batch: dict[str, Any],
        retry_sites: list[dict[str, Any]],
        next_attempt: int,
        max_attempts: int,
        create_batch: Callable[..., dict[str, Any]],
        operation_job_search: str,
        generate_summary: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        retry_site_keys = {str(item.get("site_key") or "") for item in retry_sites if str(item.get("site_key") or "")}
        if not retry_site_keys:
            return {}
        previous_loop = previous_batch.get("evolution_loop") if isinstance(previous_batch.get("evolution_loop"), dict) else {}
        root_batch_id = str(previous_loop.get("root_batch_id") or previous_batch.get("batch_id") or "")
        previous_batch_id = str(previous_batch.get("batch_id") or "")
        next_batch = create_batch(
            session_id=str(previous_batch.get("session_id") or "cli:default"),
            turn_id=make_id("turn"),
            user_message=str(previous_batch.get("user_message") or ""),
            apply_requested=bool(previous_batch.get("apply_requested")),
            operation=str(previous_batch.get("operation") or operation_job_search),
        )
        if not next_batch:
            return {}
        next_batch_id = str(next_batch.get("batch_id") or "")
        sites = next_batch.get("sites") if isinstance(next_batch.get("sites"), dict) else {}
        updated_sites: dict[str, Any] = {}
        for site_key, row in sites.items():
            if not isinstance(row, dict):
                continue
            if str(site_key) in retry_site_keys:
                updated_sites[str(site_key)] = row
                continue
            skipped = dict(row)
            skipped["status"] = "skipped"
            skipped["reason_tag"] = "outer_evolution_not_targeted"
            skipped["message"] = "Skipped by outer evolution loop; this follow-up batch only reruns sites with refinement evidence."
            skipped["retrieve"] = {"status": "skipped", "count": 0}
            skipped["apply"] = {"status": "skipped", "attempted": 0, "submitted": 0}
            updated_sites[str(site_key)] = skipped
        next_batch["sites"] = updated_sites
        next_batch["evolution_loop"] = {
            "root_batch_id": root_batch_id,
            "previous_batch_id": previous_batch_id,
            "attempt": next_attempt,
            "max_attempts": max_attempts,
            "retry_site_keys": sorted(retry_site_keys),
            "reason": "evolution_decision_continue",
            "decisions": [
                item.get("decision")
                for item in retry_sites
                if isinstance(item.get("decision"), dict) and str((item.get("decision") or {}).get("decision_id") or "")
            ],
        }
        next_batch = self.job_store.save_batch(next_batch)
        retry_by_site = {str(item.get("site_key") or ""): item for item in retry_sites}
        for site_key in retry_site_keys:
            item = retry_by_site.get(site_key) or {}
            context_text = self.outer_loop_followup_context(
                previous_batch=previous_batch,
                site_key=site_key,
                control=item.get("control") if isinstance(item.get("control"), dict) else {},
                loop_payload=item.get("loop_payload") if isinstance(item.get("loop_payload"), dict) else {},
                decision=item.get("decision") if isinstance(item.get("decision"), dict) else {},
                next_attempt=next_attempt,
                max_attempts=max_attempts,
            )
            decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
            self.site_store.save_run_context(
                site_key,
                next_batch_id,
                {
                    "apply_loop_refinement_summary": context_text,
                    "evolution_decision": decision,
                    "outer_evolution_loop": {
                        "root_batch_id": root_batch_id,
                        "previous_batch_id": previous_batch_id,
                        "attempt": next_attempt,
                        "max_attempts": max_attempts,
                        "decision_id": str(decision.get("decision_id") or ""),
                    },
                },
            )
        decisions = [
            item.get("decision")
            for item in retry_sites
            if isinstance(item.get("decision"), dict) and str((item.get("decision") or {}).get("decision_id") or "")
        ]
        previous_batch["evolution_loop"] = {
            **previous_loop,
            "root_batch_id": root_batch_id,
            "attempt": max(1, next_attempt - 1),
            "max_attempts": max_attempts,
            "next_batch_id": next_batch_id,
            "next_retry_site_keys": sorted(retry_site_keys),
            "continued_reason": "evolution_decision_continue",
            "decisions": decisions,
        }
        previous_batch["status"] = "partial_completed"
        previous_batch["reason_tag"] = "outer_evolution_continued"
        previous_batch = self.job_store.save_batch(previous_batch)
        generate_summary(previous_batch)
        self.job_store.append_event(
            "evolution.outer_batch.created",
            {
                "root_batch_id": root_batch_id,
                "previous_batch_id": previous_batch_id,
                "next_batch_id": next_batch_id,
                "attempt": next_attempt,
                "max_attempts": max_attempts,
                "site_keys": sorted(retry_site_keys),
                "decision_ids": [str(row.get("decision_id") or "") for row in decisions if isinstance(row, dict)],
            },
        )
        return next_batch

    def outer_loop_followup_context(
        self,
        *,
        previous_batch: dict[str, Any],
        site_key: str,
        control: dict[str, Any],
        loop_payload: dict[str, Any],
        decision: dict[str, Any] | None = None,
        next_attempt: int,
        max_attempts: int,
    ) -> str:
        previous_batch_id = str(previous_batch.get("batch_id") or "")
        artifacts = loop_payload.get("artifacts") if isinstance(loop_payload.get("artifacts"), dict) else {}
        previous_context = self.site_store.load_run_context(site_key, previous_batch_id)
        previous_summary = (
            str(previous_context.get("apply_loop_refinement_summary") or "").strip()
            if isinstance(previous_context, dict)
            else ""
        )
        lines = [
            "Between-batch evolution guidance from the previous apply batch:",
            f"- previous_batch_id={previous_batch_id}",
            f"- outer_attempt={next_attempt}/{max_attempts}",
            f"- action={control.get('action')}",
            f"- pattern={control.get('failure_pattern')}",
            f"- gap={control.get('gap_type') or control.get('block_reason_type')}",
        ]
        decision_payload = decision if isinstance(decision, dict) else {}
        if decision_payload:
            lines.extend(
                [
                    f"- evolution_decision_id={decision_payload.get('decision_id')}",
                    f"- decision_verdict={decision_payload.get('verdict')}",
                    f"- decision_target={decision_payload.get('target_ref')}",
                ]
            )
            overlay = str(decision_payload.get("proposal_overlay") or "").strip()
            if overlay:
                lines.extend(["", "Active evolution decision:", overlay])
            validation_plan = str(decision_payload.get("validation_plan") or "").strip()
            if validation_plan:
                lines.extend(["", f"Validation plan: {validation_plan}"])
        action_card = str(artifacts.get("action_card") or "")
        if action_card:
            lines.append(f"- action_card={action_card}")
        evidence = str(control.get("evidence") or "").strip()
        if evidence:
            lines.append(f"- evidence={evidence}")
        if previous_summary:
            lines.extend(["", previous_summary])
        lines.extend(
            [
                "",
                "Use this evidence before repeating the apply workflow. The next batch must change strategy based on the prior evidence; do not repeat the exact same failed upload/click chain unchanged.",
            ]
        )
        return "\n".join(lines).strip()

    def loop_control_pattern_attempts_in_batch(self, *, site_key: str, batch_id: str, phase: str, pattern: str) -> int:
        normalized_phase = str(phase or "").strip()
        normalized_pattern = str(pattern or "").strip()
        if not normalized_pattern:
            return 0
        count = 0
        for row in self.run_job_rows(site_key, batch_id):
            control = loop_control_from_row(row)
            if not control:
                continue
            if normalized_phase and normalized_phase != "apply":
                continue
            if str(control.get("failure_pattern") or "") == normalized_pattern:
                count += 1
        return count

    def loop_recent_trace_context(self, *, trace_ref: Any, phase: str = "apply", limit: int = 8) -> dict[str, list[str]]:
        path = self.trace_path_for_ref(trace_ref)
        if path is None or not path.exists():
            return {"tool_chain": [], "outputs": []}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {"tool_chain": [], "outputs": []}
        events: list[dict[str, Any]] = []
        for line in lines[-400:]:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if phase and str(event.get("phase") or "") != phase:
                continue
            events.append(event)
        selected = events[-max(1, int(limit or 1)) :]
        tool_chain: list[str] = []
        outputs: list[str] = []
        for event in selected:
            tool = str(event.get("tool_name") or "").strip()
            result = str(event.get("result") or "").strip()
            if tool:
                tool_chain.append(f"{tool}:{result or 'unknown'}")
            output = self.compact_trace_output(event)
            if output:
                outputs.append(output)
        return {"tool_chain": tool_chain, "outputs": outputs[-4:]}

    @staticmethod
    def compact_trace_output(event: dict[str, Any]) -> str:
        tool = str(event.get("tool_name") or "").strip()
        args = event.get("arguments") if isinstance(event.get("arguments"), dict) else {}
        output = str(event.get("output") or "").strip()
        page_line = ""
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith("- Page URL:") or stripped.startswith("### Modal state") or "[File chooser]" in stripped:
                page_line = stripped
                break
        label = str(args.get("element") or args.get("text") or args.get("url") or args.get("paths") or "").strip()
        parts = [item for item in (tool, label, page_line) if item]
        text = " | ".join(parts)
        return text[:260]

    @staticmethod
    def loop_next_iteration_guidance(
        *,
        control: dict[str, Any],
        trace_context: dict[str, list[str]],
        job_row: dict[str, Any],
    ) -> str:
        explicit_hint = str(control.get("refinement_hint") or "").strip()
        if explicit_hint:
            return explicit_hint
        chain = trace_context.get("tool_chain") if isinstance(trace_context.get("tool_chain"), list) else []
        hints: list[str] = []
        if any("browser_click" in str(item) for item in chain) and not any("update_jobs:ok" in str(item) for item in chain):
            hints.append(
                "The previous item had browser actions but no terminal `update_jobs` outcome. On the next item, use a fresh live-page read and finish with either a terminal job state or a structured loop-control gap."
            )
        return " ".join(hints)

    def related_accepted_lessons_summary(self, *, site_key: str, phase: str, limit: int = 5) -> str:
        try:
            store = BrowserControlLessonStore(self.workspace)
            lessons = store.accepted(site_key=site_key, phase=phase, limit=limit)
            if not lessons:
                lessons = store.accepted(phase=phase, scope="site_skill_evolution", limit=limit)
        except Exception:
            lessons = []
        if not lessons:
            return ""
        return render_lessons_markdown(lessons, title="Relevant Accepted Lessons", limit=limit).strip()

    def create_loop_control_solution_request(
        self,
        *,
        artifacts: dict[str, Any],
        context_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        card_id = str(artifacts.get("action_card_id") or "").strip()
        if not card_id:
            return {}
        try:
            result = create_solution_request_for_action_card(
                project_root=self.project_root,
                workspace=self.workspace,
                card_id=card_id,
                context_overrides=context_overrides or {},
            )
        except (EvolutionSolutionError, Exception) as exc:
            return {"error": str(exc), "action_card_id": card_id}
        return {
            "run_id": str(result.get("run_id") or ""),
            "status": str(result.get("status") or ""),
            "action_card_id": card_id,
            "solution_request": str(result.get("solution_request") or ""),
            "proposal_output_path": str(result.get("proposal_output_path") or ""),
            "evidence_pack": str(result.get("evidence_pack") or ""),
        }

    def persist_loop_control_guidance(
        self,
        *,
        site_key: str,
        batch_id: str,
        control: dict[str, Any],
        job_row: dict[str, Any],
        artifacts: dict[str, Any],
        trace_context: dict[str, list[str]] | None = None,
        next_iteration_guidance: str = "",
        accepted_lessons_summary: str = "",
    ) -> dict[str, Any]:
        save_run_context = getattr(self.site_store, "save_run_context", None)
        if not callable(save_run_context):
            return {}
        item = {
            "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "action": str(control.get("action") or ""),
            "loop_scope": str(control.get("loop_scope") or ""),
            "gap_type": self.loop_control_gap_type(control),
            "failure_pattern": str(control.get("failure_pattern") or ""),
            "target": str(control.get("target") or control.get("recommended_target") or ""),
            "resume_policy": str(control.get("resume_policy") or ""),
            "current_item_ref": str(control.get("current_item_ref") or ""),
            "title": str(job_row.get("title") or ""),
            "url": str(job_row.get("url") or ""),
            "evidence": str(control.get("evidence") or "")[:700],
            "refinement_hint": str(control.get("refinement_hint") or "")[:700],
            "next_iteration_guidance": str(next_iteration_guidance or job_row.get("_loop_next_iteration_guidance") or "")[:1200],
            "recent_tool_chain": list((trace_context or {}).get("tool_chain") or [])[-8:],
            "last_tool_outputs": list((trace_context or {}).get("outputs") or [])[-4:],
            "accepted_lessons_summary": str(accepted_lessons_summary or "")[:2000],
            "action_card": str(artifacts.get("action_card") or ""),
            "candidate_id": str(artifacts.get("candidate_id") or ""),
            "evidence_id": str(artifacts.get("evidence_id") or ""),
        }
        memory = self.persist_loop_control_evolution_memory(
            site_key=site_key,
            batch_id=batch_id,
            control=control,
            job_row=job_row,
            item=item,
            artifacts=artifacts,
        )
        if memory:
            item["evolution_memory_id"] = str(memory.get("memory_id") or "")
            item["proposal_status"] = str((memory.get("proposal") or {}).get("proposal_status") or "")
        try:
            current = self.site_store.load_run_context(site_key, batch_id)
        except Exception:
            current = {}
        previous = current.get("apply_loop_refinement_guidance") if isinstance(current, dict) else []
        if not isinstance(previous, list):
            previous = []
        previous.append(item)
        guidance = previous[-5:]
        summary_lines = [
            "Short-term apply-loop guidance from earlier items in this same batch:",
            *[
                (
                    f"- pattern={row.get('failure_pattern')}; gap={row.get('gap_type')}; "
                    f"target={row.get('target')}; action_card={row.get('action_card')}; "
                    f"chain={' -> '.join(row.get('recent_tool_chain') or [])}; "
                    f"next={row.get('next_iteration_guidance') or row.get('refinement_hint')}"
                )
                for row in guidance
                if isinstance(row, dict)
            ],
            "Use these as run-local strategy updates before processing the next apply item. Do not repeat a known failed strategy.",
        ]
        lesson_lines = [str(row.get("accepted_lessons_summary") or "").strip() for row in guidance if isinstance(row, dict)]
        lesson_lines = [line for line in lesson_lines if line]
        if lesson_lines:
            summary_lines.extend(["", "Relevant accepted lessons:", lesson_lines[-1]])
        try:
            save_run_context(
                site_key,
                batch_id,
                {
                    "apply_loop_refinement_guidance": guidance,
                    "apply_loop_refinement_summary": "\n".join(summary_lines).strip(),
                },
            )
        except Exception:
            return memory if isinstance(memory, dict) else {}
        return memory if isinstance(memory, dict) else {}

    def persist_loop_control_evolution_memory(
        self,
        *,
        site_key: str,
        batch_id: str,
        control: dict[str, Any],
        job_row: dict[str, Any],
        item: dict[str, Any],
        artifacts: dict[str, Any],
    ) -> dict[str, Any]:
        pattern = str(control.get("failure_pattern") or item.get("failure_pattern") or "unknown_loop_pattern")
        evidence = str(control.get("evidence") or item.get("evidence") or "")
        next_guidance = str(item.get("next_iteration_guidance") or control.get("refinement_hint") or "")
        action = str(control.get("action") or "").strip()
        avoid_patterns = self.loop_memory_avoid_patterns(pattern=pattern, evidence=evidence)
        recommended_patterns = self.loop_memory_recommended_patterns(
            pattern=pattern,
            evidence=evidence,
            next_guidance=next_guidance,
        )
        proposal_id = f"pending_run_local_prop_{str(artifacts.get('evidence_id') or make_id('evidence')).replace('evidence_', '')}"
        prompt_overlay = str(next_guidance or "").strip()
        summary = (
            f"{site_key} apply loop evidence for `{pattern}` needs a concrete solution proposal. "
            "Python records evidence and requests a solution; it does not invent or materialize workflow strategy."
        )
        unit = build_loop_evolution_memory(
            candidate_id="site_apply_loop_control",
            scope=f"batch:{batch_id}:site:{site_key}:apply",
            site_key=site_key,
            phase="apply",
            lifecycle="run_local",
            status="active",
            pattern=pattern,
            evidence=evidence,
            summary=summary,
            avoid_patterns=avoid_patterns,
            recommended_patterns=recommended_patterns,
            source={
                "batch_id": batch_id,
                "job_id": str(job_row.get("job_id") or ""),
                "title": str(job_row.get("title") or ""),
                "url": str(job_row.get("url") or ""),
                "evidence_id": str(artifacts.get("evidence_id") or ""),
                "candidate_id": str(artifacts.get("candidate_id") or ""),
                "action_card": str(artifacts.get("action_card") or ""),
            },
            target=str(control.get("target") or control.get("recommended_target") or ""),
            confidence=0.55,
            proposal={
                "proposal_id": proposal_id,
                "proposal_kind": "run_local_overlay",
                "prompt_overlay": prompt_overlay,
                "expected_validation": (
                    f"The next apply target should not repeat `{pattern}` unchanged; it should reach a terminal "
                    "update_jobs state or produce a new structured loop-control gap."
                ),
                "source_evidence_id": str(artifacts.get("evidence_id") or ""),
                "target_ref": str(artifacts.get("target_ref") or ""),
                "materialized_change": {},
            },
        )
        try:
            return EvolutionMemoryStore(self.workspace).upsert(unit)
        except Exception:
            return {}

    def persist_loop_control_workflow_memory(
        self,
        *,
        site_key: str,
        batch_id: str,
        turn_id: str,
        control: dict[str, Any],
        artifacts: dict[str, Any],
    ) -> None:
        summary = (
            f"Loop-control gap recorded: action={control.get('action')}; "
            f"pattern={control.get('failure_pattern')}; gap={self.loop_control_gap_type(control)}; "
            f"hint={control.get('refinement_hint') or ''}; action_card={artifacts.get('action_card') or ''}"
        )
        try:
            WorkflowMemoryStore(self.workspace).update_phase(
                site_key=site_key,
                phase="apply",
                status="failed",
                batch_id=batch_id,
                turn_id=turn_id,
                current_url=str(control.get("current_item_ref") or ""),
                trace_ref="",
                reason_tag=f"loop_control_{control.get('action') or 'recorded'}",
                summary=summary,
            )
        except Exception:
            return

    def _loop_control_site_row(
        self,
        *,
        existing: dict[str, Any],
        site_key: str,
        batch_id: str,
        last_result: Any | None,
        control: dict[str, Any],
        action: str,
        pattern: str,
        artifacts: dict[str, Any],
        memory: dict[str, Any],
        materialized: bool,
        solution_request: dict[str, Any],
        decision: dict[str, Any],
        transition: Any,
        should_pause: bool,
    ) -> dict[str, Any]:
        retrieve = dict(existing.get("retrieve") or {})
        apply = dict(existing.get("apply") or {})
        counters = self.apply_counters_from_run(site_key, batch_id)
        waiting_solution = bool(
            should_pause
            and transition.requires_materialized_change
            and str(solution_request.get("solution_request") or "").strip()
        )
        apply_status = "waiting_solution" if waiting_solution else ("blocked" if should_pause else "running")
        apply.update(
            {
                "status": apply_status,
                **self.apply_counter_payload(counters),
                "loop_control": {
                    "action": action,
                    "failure_pattern": pattern,
                    "block_reason_type": str(control.get("block_reason_type") or ""),
                    "gap_type": str(control.get("gap_type") or ""),
                    "recommended_target": str(control.get("recommended_target") or ""),
                    "target": str(control.get("target") or ""),
                    "resume_policy": str(control.get("resume_policy") or ""),
                    "current_item_ref": str(control.get("current_item_ref") or ""),
                    "evidence": str(control.get("evidence") or ""),
                    "refinement_hint": str(control.get("refinement_hint") or ""),
                    "proposal_status": str((memory.get("proposal") or {}).get("proposal_status") or ""),
                    "materialized_change": bool(materialized),
                    "waiting_solution": waiting_solution,
                    "solution_run_id": str(solution_request.get("run_id") or ""),
                    "solution_request": str(solution_request.get("solution_request") or ""),
                    "proposal_output_path": str(solution_request.get("proposal_output_path") or ""),
                    "solution_request_error": str(solution_request.get("error") or ""),
                    "attempts": self.loop_control_pattern_attempts_in_batch(
                        site_key=site_key,
                        batch_id=batch_id,
                        phase="apply",
                        pattern=pattern,
                    ),
                    "should_pause": should_pause,
                    "item_loop_transition": transition.as_dict(),
                    "artifacts": artifacts,
                    "evolution_decision": decision,
                },
            }
        )
        retrieve["count"] = max(int(retrieve.get("count") or 0), counters["retrieved"])
        current_url = str(
            getattr(last_result, "current_url", "") or existing.get("current_url") or existing.get("entry_url") or ""
        )
        trace_ref = str(getattr(last_result, "trace_ref", "") or existing.get("trace_ref") or "")
        step_count = int(getattr(last_result, "step_count", 0) or existing.get("step_count") or 0)
        reason_tag = "loop_control_" + (action or "pause")
        if should_pause:
            reason_tag = transition.reason_tag
        if waiting_solution:
            reason_tag = "item_loop_waiting_solution"
        if should_pause:
            if bool(transition.requires_materialized_change):
                request_path = str(solution_request.get("solution_request") or "")
                proposal_path = str(solution_request.get("proposal_output_path") or "")
                request_note = f" Solution request: {request_path}." if request_path else ""
                proposal_note = f" Proposal output: {proposal_path}." if proposal_path else ""
                message = (
                    f"Item loop held after `{action}` for pattern `{pattern}` because the next item must not run "
                    "under the same stale strategy before a concrete run-local materialized_change exists."
                    f"{request_note}{proposal_note}"
                )
            else:
                message = (
                    f"Apply loop paused after `{action}` for pattern `{pattern}`. "
                    "Review the generated action card/evidence before continuing."
                )
        else:
            message = (
                f"Apply loop recorded `{action}` for pattern `{pattern}` and will continue with the next apply item. "
                f"{transition.message}"
            )
        return {
            **existing,
            "status": "waiting_solution" if waiting_solution else ("blocked" if should_pause else "running"),
            "reason_tag": reason_tag,
            "message": message,
            "solution_run_id": str(solution_request.get("run_id") or "") if waiting_solution else "",
            "solution_request": str(solution_request.get("solution_request") or "") if waiting_solution else "",
            "proposal_output_path": str(solution_request.get("proposal_output_path") or "") if waiting_solution else "",
            "current_phase": "apply",
            "current_url": current_url,
            "trace_ref": trace_ref,
            "step_count": step_count,
            "retrieve": retrieve,
            "apply": apply,
        }

    @staticmethod
    def loop_control_gap_type(control: dict[str, Any]) -> str:
        return str(control.get("gap_type") or control.get("block_reason_type") or "").strip().lower()

    @staticmethod
    def loop_memory_avoid_patterns(*, pattern: str, evidence: str) -> list[str]:
        return ["Do not repeat a known failed apply strategy on the next target without using the run-local guidance."]

    @staticmethod
    def loop_memory_recommended_patterns(*, pattern: str, evidence: str, next_guidance: str) -> list[str]:
        recommended: list[str] = []
        if next_guidance:
            recommended.append(next_guidance)
        if not recommended:
            recommended.append("Use the loop-control evidence to change strategy before the next apply target.")
        return recommended

    @staticmethod
    def _run_local_proposal_validation_result(*, row: dict[str, Any], proposal_pattern: str = "") -> str:
        application_status = str(row.get("application_status") or "").strip().lower()
        decision_status = str(row.get("decision_status") or "").strip().lower()
        control = loop_control_from_row(row)
        if application_status in {"submitted", "already_applied"}:
            return "validated_terminal_success"
        if decision_status == "filtered_out" or application_status == "filtered_out":
            return "validated_terminal_filtered_out"
        if control:
            current_pattern = str(control.get("failure_pattern") or "").strip()
            if proposal_pattern and current_pattern == proposal_pattern:
                return "repeated_same_failure"
            return "new_loop_failure"
        if application_status in {"blocked", "apply_failed"}:
            return "terminal_failure_without_loop_control"
        return "unknown"
