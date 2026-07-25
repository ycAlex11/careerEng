"""Outer batch evolution orchestration.

This module wraps the stable job-flow execution layer. It owns generic
batch-level evolution continuation, but it does not infer site workflow,
form-filling strategy, matching policy, or browser behavior.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.evolution.apply import EvolutionApplyError, apply_evolution_run
from careereng.evolution.memory_units import (
    EvolutionMemoryStore,
    evolution_memory_has_materialized_change,
    run_local_units_for_batch_site,
)
from careereng.evolution.loop_control import (
    LOOP_ACTION_TRIGGER_REFINEMENT,
    loop_control_from_row,
    loop_control_is_human_only_gap,
)
from careereng.evolution.solution_bridge import EvolutionSolutionBridgeError, ProviderSolutionBridge
from careereng.evolution.work_items import create_site_exploration_synthesis_card
from careereng.utils import read_json


SOLUTION_LEVEL_ITEM_LOOP = "item_loop"
SOLUTION_LEVEL_OUTER_SYNTHESIS = "outer_synthesis"
class BatchEvolutionOrchestrator:
    """Run batch execution through the evolution outer loop.

    JobFlow remains the execution layer. This orchestrator consumes the generic
    loop evidence already written by JobFlow/EvolutionLoopEngine and decides whether
    another batch attempt should be created.
    """

    def __init__(self, job_flow: Any, *, solution_bridge: Any | None = None, auto_solve: bool | None = None):
        self.job_flow = job_flow
        self.loop_engine = job_flow.loop_engine
        self.job_store = job_flow.job_store
        should_auto_solve = bool(auto_solve) if auto_solve is not None else False
        if solution_bridge is not None:
            self.solution_bridge = solution_bridge
        elif should_auto_solve:
            self.solution_bridge = self._default_solution_bridge()
        else:
            self.solution_bridge = None
        self.auto_solve = should_auto_solve and self.solution_bridge is not None

    @property
    def max_outer_attempts(self) -> int:
        budgets = getattr(self.job_flow, "browser_budgets", None)
        return max(
            1,
            int(getattr(budgets, "outer_max_attempts", getattr(budgets, "loop_control_outer_batch_attempts", 3)) or 3),
        )

    def _default_solution_bridge(self) -> ProviderSolutionBridge | None:
        provider = getattr(self.job_flow, "solution_provider", None)
        model = str(getattr(self.job_flow, "solution_model", "") or "")
        if provider is None or not callable(getattr(provider, "chat", None)):
            return None
        return ProviderSolutionBridge(
            project_root=Path(self.job_flow.project_root),
            workspace=Path(self.job_store.workspace),
            provider=provider,
            model=model,
        )

    def run_batch_with_outer_loop(self, batch_id: str) -> str:
        current_batch_id = str(batch_id or "")
        last_reply = ""
        while current_batch_id:
            last_reply = self.job_flow.run_batch(current_batch_id)
            batch = self.job_store.load_batch(current_batch_id)
            solved_reply = self.solve_waiting_solution_and_continue(batch)
            if solved_reply:
                return solved_reply
            batch, synthesis_created = self.create_synthesis_request_if_needed(batch)
            if synthesis_created:
                solved_reply = self.solve_waiting_solution_and_continue(batch)
                if solved_reply:
                    return solved_reply
                return self.job_flow._format_batch_summary(batch)
            next_batch = self.create_followup_batch_if_needed(batch)
            next_batch_id = str(next_batch.get("batch_id") or "")
            if not next_batch_id:
                return last_reply
            current_batch_id = next_batch_id
        return last_reply

    def continue_after_solution(self, batch_id: str) -> str:
        batch = self.job_store.load_batch(str(batch_id or ""))
        if not batch:
            return f"batch={batch_id} status=failed"
        solved_reply = self.solve_waiting_solution_and_continue(batch)
        if solved_reply:
            return solved_reply
        outer_solution_ready = self._batch_has_applied_outer_solution_to_consume(batch)
        batch = self.mark_applied_solution_for_outer_loop(batch)
        if outer_solution_ready:
            self.job_flow._generate_batch_report_if_possible(batch)
            self.job_flow._generate_workflow_evolution_summary_if_possible(batch)
            return self.job_flow._format_batch_summary(batch)
        if self._batch_has_item_loop_continuation(batch):
            reply = self.continue_current_item_loops(batch)
            solved_reply = self.solve_waiting_solution_and_continue(self.job_store.load_batch(str(batch.get("batch_id") or "")))
            return solved_reply or reply
        batch, resumed = self.resume_materialized_run_local_sites(batch)
        if resumed:
            reply = self.continue_current_item_loops(batch)
            solved_reply = self.solve_waiting_solution_and_continue(self.job_store.load_batch(str(batch.get("batch_id") or "")))
            return solved_reply or reply
        batch, thresholded = self.reconcile_item_loop_thresholds(batch)
        if thresholded:
            batch, _ = self.create_synthesis_request_if_needed(batch)
            solved_reply = self.solve_waiting_solution_and_continue(batch)
            if solved_reply:
                return solved_reply
            return self.job_flow._format_batch_summary(batch)
        if self._batch_has_unapplied_pending_solution(batch):
            return self.job_flow._format_batch_summary(batch)
        next_batch = self.create_followup_batch_if_needed(batch)
        if next_batch:
            return self.run_batch_with_outer_loop(str(next_batch.get("batch_id") or ""))
        batch, synthesis_created = self.create_synthesis_request_if_needed(batch)
        if synthesis_created:
            solved_reply = self.solve_waiting_solution_and_continue(batch)
            if solved_reply:
                return solved_reply
            return self.job_flow._format_batch_summary(batch)
        return self.run_batch_with_outer_loop(str(batch.get("batch_id") or batch_id))

    def solve_waiting_solution_and_continue(self, batch: dict[str, Any]) -> str:
        """Write/apply pending proposals at batch boundaries, then continue.

        This is orchestration only. The bridge delegates proposal content to an
        LLM/Codex-compatible provider and ``apply_evolution_run`` performs the
        existing rollbackable apply step.
        """

        if not self.auto_solve or self.solution_bridge is None:
            return ""
        if not isinstance(batch, dict) or not batch:
            return ""
        batch_id = str(batch.get("batch_id") or "")
        run_ids = self._waiting_solution_run_ids(batch)
        if not run_ids:
            return ""
        applied_runs: list[str] = []
        for run_id in run_ids:
            if self._solution_run_is_applied(run_id):
                continue
            try:
                self.solution_bridge.write_proposal_for_run(run_id)
                apply_evolution_run(
                    workspace=Path(self.job_store.workspace),
                    project_root=Path(self.job_flow.project_root),
                    run_id=run_id,
                )
            except (EvolutionSolutionBridgeError, EvolutionApplyError, ValueError, RuntimeError) as exc:
                self.job_store.append_event(
                    "evolution.solution_bridge.failed",
                    {
                        "batch_id": batch_id,
                        "run_id": run_id,
                        "error": str(exc),
                    },
                )
                return ""
            applied_runs.append(run_id)
            self.job_store.append_event(
                "evolution.solution_bridge.applied",
                {
                    "batch_id": batch_id,
                    "run_id": run_id,
                },
            )
        if not applied_runs:
            return ""
        return self.continue_after_solution(batch_id)

    def continue_current_item_loops(self, batch: dict[str, Any]) -> str:
        """Continue from materialized item-loop continuation points.

        This intentionally does not call the full batch/site workflow head.
        The continuation contract is generic; the only item-loop executor that
        exists today is the apply-phase item loop.
        """

        if not isinstance(batch, dict) or not batch:
            return "batch= status=failed"
        batch_id = str(batch.get("batch_id") or "")
        session_id = str(batch.get("session_id") or "cli:default")
        turn_id = str(batch.get("turn_id") or "")
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}

        def save_site(site_key: str, updated: dict[str, Any], *, generate_report: bool) -> dict[str, Any]:
            nonlocal batch
            latest = self.job_store.load_batch(batch_id) if batch_id else batch
            batch = self.job_store.update_site(latest or batch, site_key, updated)
            batch["status"] = self.job_flow._compute_batch_status(batch)
            batch = self.job_store.save_batch(batch)
            if generate_report:
                self.job_flow._generate_batch_report_if_possible(batch)
                self.job_flow._generate_workflow_evolution_summary_if_possible(batch)
            latest_sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
            latest_row = latest_sites.get(site_key)
            return dict(latest_row) if isinstance(latest_row, dict) else dict(updated)

        continued = False
        for site_key, row in list(sites.items()):
            if not isinstance(row, dict) or not self._site_has_item_loop_continuation(row):
                continue
            continuation = self._item_loop_continuation(row)
            phase = str(continuation.get("phase") or "").strip()
            if phase != "apply":
                continue
            updated = self.job_flow.continue_item_loop(
                site_key=str(site_key),
                current=row,
                batch_id=batch_id,
                session_id=session_id,
                turn_id=turn_id,
                continuation=continuation,
                progress_callback=lambda next_row, key=str(site_key): save_site(key, next_row, generate_report=True),
            )
            save_site(str(site_key), updated, generate_report=True)
            continued = True

        if not continued:
            batch["status"] = self.job_flow._compute_batch_status(batch)
            batch = self.job_store.save_batch(batch)
            self.job_flow._generate_batch_report_if_possible(batch)
            self.job_flow._generate_workflow_evolution_summary_if_possible(batch)
        if continued:
            latest_sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
            for site_key, row in list(latest_sites.items()):
                if not isinstance(row, dict) or not self._site_has_item_loop_continuation(row):
                    continue
                updated = {k: v for k, v in row.items() if k != "continuation"}
                batch = self.job_store.update_site(batch, str(site_key), updated)
            batch["status"] = self.job_flow._compute_batch_status(batch)
            batch = self.job_store.save_batch(batch)
            self.job_flow._generate_batch_report_if_possible(batch)
            self.job_flow._generate_workflow_evolution_summary_if_possible(batch)
        batch, _ = self.create_synthesis_request_if_needed(batch)
        return self.job_flow._format_batch_summary(batch)

    def create_synthesis_request_if_needed(self, batch: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Create Codex synthesis work orders for stopped units with evolution evidence.

        This is orchestration plumbing only. It does not decide the solution;
        it packages already-recorded loop evidence into the existing Codex
        solution-request/evidence-pack path.
        """

        if not isinstance(batch, dict) or not batch:
            return batch, False
        if str(batch.get("status") or "") in {"running", "cancelled"}:
            return batch, False

        batch_id = str(batch.get("batch_id") or "")
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        changed = False
        for site_key, row in list(sites.items()):
            if not isinstance(row, dict):
                continue
            if self._site_requires_exploration_synthesis(row):
                result = self._create_exploration_synthesis_request(
                    batch=batch,
                    site_key=str(site_key),
                    row=row,
                )
            elif self._site_has_stopped_evolution_evidence(row):
                _container_key, container = self._evolution_container(row)
                loop_payload = container.get("loop_control") if isinstance(container.get("loop_control"), dict) else {}
                artifacts = loop_payload.get("artifacts") if isinstance(loop_payload.get("artifacts"), dict) else {}
                result = self.loop_engine.create_loop_control_solution_request(
                    artifacts=artifacts,
                    context_overrides={
                        "solution_level": SOLUTION_LEVEL_OUTER_SYNTHESIS,
                        "solution_request_kind": "synthesis_work_order",
                    },
                )
            else:
                continue
            if str(result.get("error") or "").strip():
                raise RuntimeError(str(result.get("error") or "Failed to create evolution synthesis request."))
            if not str(result.get("solution_request") or "").strip():
                continue
            updated = self._synthesis_waiting_solution_site_row(current=row, request=result)
            batch = self.job_store.update_site(batch, str(site_key), updated)
            changed = True
            sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}

        if changed:
            batch["status"] = self.job_flow._compute_batch_status(batch)
            batch = self.job_store.save_batch(batch)
            self.job_flow._generate_batch_report_if_possible(batch)
            self.job_flow._generate_workflow_evolution_summary_if_possible(batch)
        return batch, changed

    def _create_exploration_synthesis_request(
        self,
        *,
        batch: dict[str, Any],
        site_key: str,
        row: dict[str, Any],
    ) -> dict[str, Any]:
        """Package a completed exploration run for Codex without inferring readiness."""

        batch_id = str(batch.get("batch_id") or "")
        card = create_site_exploration_synthesis_card(
            workspace=Path(self.job_store.workspace),
            project_root=Path(self.job_flow.project_root),
            site_key=site_key,
            site_name=str(row.get("site_name") or site_key),
            batch_id=batch_id,
            skill_path=str(row.get("skill_path") or ""),
        )
        return self.loop_engine.create_loop_control_solution_request(
            artifacts={"action_card_id": str(card.get("card_id") or "")},
            context_overrides={
                "solution_level": SOLUTION_LEVEL_OUTER_SYNTHESIS,
                "solution_request_kind": "synthesis_work_order",
                "exploration_completion": True,
                "site_key": site_key,
                "batch_id": batch_id,
            },
        )

    def reconcile_item_loop_thresholds(self, batch: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if not isinstance(batch, dict) or not batch:
            return batch, False
        batch_id = str(batch.get("batch_id") or "")
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        changed = False
        for site_key, row in list(sites.items()):
            if not isinstance(row, dict) or not self._site_is_waiting_solution(row):
                continue
            apply_payload = row.get("apply") if isinstance(row.get("apply"), dict) else {}
            loop_control = apply_payload.get("loop_control") if isinstance(apply_payload.get("loop_control"), dict) else {}
            if str(loop_control.get("action") or "") != LOOP_ACTION_TRIGGER_REFINEMENT:
                continue
            phase = str(loop_control.get("phase") or row.get("current_phase") or "apply").strip() or "apply"
            pattern = str(loop_control.get("failure_pattern") or "").strip()
            if not pattern:
                continue
            attempts = self.loop_engine.loop_control_pattern_attempts_in_batch(
                site_key=str(site_key),
                batch_id=batch_id,
                phase=phase,
                pattern=pattern,
            )
            limit = max(1, int(self.loop_engine.refinement_attempts_per_batch or 1))
            if attempts < limit:
                continue
            updated = self._threshold_site_row(
                current=row,
                attempts=attempts,
                limit=limit,
                phase=phase,
                pattern=pattern,
            )
            batch = self.job_store.update_site(batch, str(site_key), updated)
            changed = True
            sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        if changed:
            batch["status"] = self.job_flow._compute_batch_status(batch)
            batch = self.job_store.save_batch(batch)
            self.job_flow._generate_batch_report_if_possible(batch)
            self.job_flow._generate_workflow_evolution_summary_if_possible(batch)
        return batch, changed

    def retry_sites(self, batch: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(batch, dict):
            return []
        return self.loop_engine.outer_loop_retry_sites(
            batch=batch,
            operation_job_search=str(getattr(self.job_flow, "OPERATION_JOB_SEARCH", "job_search")),
            normalize_operation=self.job_flow._normalize_operation,
        )

    def create_followup_batch_if_needed(self, batch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(batch, dict) or not batch:
            return {}
        if self._batch_has_pending_solution(batch):
            return {}
        existing_loop = batch.get("evolution_loop") if isinstance(batch.get("evolution_loop"), dict) else {}
        if str(existing_loop.get("next_batch_id") or "").strip():
            return {}
        retry_sites = self.retry_sites(batch)
        if not retry_sites:
            return {}
        current_attempt = self.loop_engine.outer_loop_attempt(batch)
        if current_attempt >= self.max_outer_attempts:
            return {}
        return self.loop_engine.create_outer_loop_followup_batch(
            previous_batch=batch,
            retry_sites=retry_sites,
            next_attempt=current_attempt + 1,
            max_attempts=self.max_outer_attempts,
            create_batch=self.job_flow.create_batch,
            operation_job_search=str(getattr(self.job_flow, "OPERATION_JOB_SEARCH", "job_search")),
            generate_summary=self.job_flow._generate_workflow_evolution_summary_if_possible,
        )

    def _batch_has_pending_solution(self, batch: dict[str, Any]) -> bool:
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        for row in sites.values():
            if not isinstance(row, dict):
                continue
            if self._site_is_waiting_solution(row):
                return True
            run_id = self._solution_run_id(row)
            if not run_id:
                continue
            if self._solution_level(row) == SOLUTION_LEVEL_OUTER_SYNTHESIS and not self._solution_run_is_applied(run_id):
                return True
        return False

    def _batch_has_unapplied_pending_solution(self, batch: dict[str, Any]) -> bool:
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        for row in sites.values():
            if not isinstance(row, dict) or not self._site_is_waiting_solution(row):
                continue
            run_id = self._solution_run_id(row)
            if not run_id or not self._solution_run_is_applied(run_id):
                return True
        return False

    def resume_materialized_run_local_sites(self, batch: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if not isinstance(batch, dict) or not batch:
            return batch, False
        batch_id = str(batch.get("batch_id") or "")
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        changed = False
        for site_key, row in list(sites.items()):
            if not isinstance(row, dict):
                continue
            batch, updated, resumed = self.resume_waiting_solution_if_materialized(
                batch=batch,
                site_key=str(site_key),
                current=row,
                batch_id=batch_id,
            )
            changed = changed or resumed
            sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
            sites[str(site_key)] = updated
        return batch, changed

    def resume_waiting_solution_if_materialized(
        self,
        *,
        batch: dict[str, Any],
        site_key: str,
        current: dict[str, Any],
        batch_id: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        if not self._site_is_waiting_solution(current):
            return batch, current, False
        if self._solution_level(current) == SOLUTION_LEVEL_OUTER_SYNTHESIS:
            return batch, current, False
        active = self._active_materialized_run_local_proposals(
            site_key=site_key,
            batch_id=batch_id or str(batch.get("batch_id") or ""),
        )
        if not active:
            return batch, current, False
        updated = self._materialized_site_row(
            current=current,
            proposal=active[-1],
            resume_current_batch=True,
            solution_run_id=self._solution_run_id(current),
            solution_level=SOLUTION_LEVEL_ITEM_LOOP,
        )
        batch = self.job_store.update_site(batch, site_key, updated)
        batch["status"] = self.job_flow._compute_batch_status(batch)
        batch = self.job_store.save_batch(batch)
        return batch, updated, True

    def mark_applied_solution_for_outer_loop(self, batch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(batch, dict) or not batch:
            return batch
        batch_id = str(batch.get("batch_id") or "")
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        changed = False
        for site_key, row in list(sites.items()):
            if not isinstance(row, dict) or not self._site_has_applied_solution_to_consume(row):
                continue
            solution_run_id = self._solution_run_id(row)
            solution_level = self._solution_level(row)
            if solution_level == SOLUTION_LEVEL_OUTER_SYNTHESIS:
                self._close_run_local_scope_for_outer_synthesis(
                    batch_id=batch_id,
                    site_key=str(site_key),
                    current=row,
                    solution_run_id=solution_run_id,
                    reason="outer synthesis consumed run-local evidence",
                )
                decision = self._outer_synthesis_site_mode_decision(
                    site_key=str(site_key),
                    solution_run_id=solution_run_id,
                )
                if decision == "ready":
                    updated = self._outer_synthesis_ready_site_row(current=row, solution_run_id=solution_run_id)
                    batch = self.job_store.update_site(batch, str(site_key), updated)
                    batch = self._drop_site_keys(batch=batch, site_key=str(site_key), keys=("continuation",))
                    changed = True
                    sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
                    continue
            active = (
                self._active_materialized_run_local_proposals(site_key=str(site_key), batch_id=batch_id)
                if solution_level == SOLUTION_LEVEL_ITEM_LOOP
                else []
            )
            updated = self._materialized_site_row(
                current=row,
                proposal=active[-1] if active else {},
                resume_current_batch=solution_level == SOLUTION_LEVEL_ITEM_LOOP,
                solution_run_id=solution_run_id,
                solution_level=solution_level,
            )
            batch = self.job_store.update_site(batch, str(site_key), updated)
            if solution_level == SOLUTION_LEVEL_OUTER_SYNTHESIS:
                batch = self._drop_site_keys(batch=batch, site_key=str(site_key), keys=("continuation",))
            changed = True
            sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        if changed:
            batch["status"] = self.job_flow._compute_batch_status(batch)
            batch = self.job_store.save_batch(batch)
        return batch

    def _outer_synthesis_site_mode_decision(self, *, site_key: str, solution_run_id: str) -> str:
        """Read an applied LLM decision; no business conclusion is made here."""

        if not solution_run_id:
            return ""
        payload = read_json(Path(self.job_store.workspace) / "evolution" / "runs" / solution_run_id / "applied_files.json")
        files = payload.get("files") if isinstance(payload.get("files"), list) else []
        for record in files:
            if not isinstance(record, dict) or str(record.get("change_type") or "") != "site_mode_update":
                continue
            if str(record.get("site_key") or "").strip() != str(site_key or "").strip():
                continue
            mode = str(record.get("mode") or "").strip().lower()
            if mode in {"ready", "exploration"}:
                return mode
        return ""

    @classmethod
    def _outer_synthesis_ready_site_row(cls, *, current: dict[str, Any], solution_run_id: str) -> dict[str, Any]:
        container_key, container = cls._evolution_container(current)
        payload = dict(container or {})
        loop_control = dict(payload.get("loop_control") or {})
        loop_control.update(
            {
                "waiting_solution": False,
                "synthesis_required": False,
                "solution_consumed": True,
                "materialized_solution_run_id": solution_run_id,
                "solution_level": SOLUTION_LEVEL_OUTER_SYNTHESIS,
                "outer_synthesis_decision": "ready",
            }
        )
        payload["loop_control"] = loop_control
        payload["status"] = "completed"
        return {
            **current,
            "status": "completed",
            "reason_tag": "outer_synthesis_ready",
            "message": "Codex outer synthesis promoted this site to normal execution.",
            container_key: payload,
        }

    def _site_has_applied_solution_to_consume(self, row: dict[str, Any]) -> bool:
        solution_run_id = self._solution_run_id(row)
        if not self._solution_run_is_applied(solution_run_id):
            return False
        _container_key, container = self._evolution_container(row)
        loop_control = container.get("loop_control") if isinstance(container.get("loop_control"), dict) else {}
        if bool(loop_control.get("solution_consumed")) and str(loop_control.get("materialized_solution_run_id") or "") == solution_run_id:
            return False
        return self._solution_level(row) in {SOLUTION_LEVEL_ITEM_LOOP, SOLUTION_LEVEL_OUTER_SYNTHESIS}

    def _batch_has_applied_outer_solution_to_consume(self, batch: dict[str, Any]) -> bool:
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        for row in sites.values():
            if not isinstance(row, dict):
                continue
            if self._solution_level(row) != SOLUTION_LEVEL_OUTER_SYNTHESIS:
                continue
            if self._site_has_applied_solution_to_consume(row):
                return True
        return False

    def _close_run_local_scope_for_outer_synthesis(
        self,
        *,
        batch_id: str,
        site_key: str,
        current: dict[str, Any],
        solution_run_id: str,
        reason: str,
    ) -> dict[str, Any]:
        batch_text = str(batch_id or "").strip()
        site_text = str(site_key or "").strip()
        if not batch_text or not site_text:
            return {"closed_count": 0, "closed_memory_ids": []}
        store = EvolutionMemoryStore(Path(self.job_store.workspace))
        units = run_local_units_for_batch_site(
            workspace=Path(self.job_store.workspace),
            site_key=site_text,
            batch_id=batch_text,
            statuses=["active"],
        )
        scopes = sorted({str(unit.get("scope") or "") for unit in units if str(unit.get("scope") or "")})
        if not scopes:
            _container_key, container = self._evolution_container(current)
            loop_control = container.get("loop_control") if isinstance(container.get("loop_control"), dict) else {}
            phase = str(loop_control.get("phase") or current.get("current_phase") or "apply").strip() or "apply"
            scopes = [f"batch:{batch_text}:site:{site_text}:{phase}"]
        closed: list[str] = []
        for scope in scopes:
            result = store.close_run_local_scope_after_synthesis(
                scope=scope,
                reason=reason,
                run_id=solution_run_id,
            )
            closed.extend(result.get("closed_memory_ids") if isinstance(result.get("closed_memory_ids"), list) else [])
        return {"closed_count": len(closed), "closed_memory_ids": closed, "scopes": scopes}

    def _active_materialized_run_local_proposals(self, *, site_key: str, batch_id: str) -> list[dict[str, Any]]:
        try:
            return [
                row
                for row in self.loop_engine.active_run_local_apply_proposals(
                    site_key=site_key,
                    batch_id=batch_id,
                    limit=1,
                )
                if evolution_memory_has_materialized_change(row)
            ]
        except Exception:
            return []

    def _materialized_site_row(
        self,
        *,
        current: dict[str, Any],
        proposal: dict[str, Any],
        resume_current_batch: bool,
        solution_run_id: str,
        solution_level: str,
    ) -> dict[str, Any]:
        container_key, container = self._evolution_container(current)
        payload = dict(container or {})
        loop_control = dict(payload.get("loop_control") or {})
        proposal_payload = proposal.get("proposal") if isinstance(proposal.get("proposal"), dict) else {}
        loop_control.update(
            {
                "waiting_solution": False,
                "materialized_change": True,
                "proposal_status": str(proposal_payload.get("proposal_status") or "applied"),
                "active_run_local_proposal_id": str(proposal_payload.get("proposal_id") or ""),
                "active_run_local_proposal_memory_id": str(proposal.get("memory_id") or ""),
                "materialized_solution_run_id": solution_run_id,
                "solution_consumed": True,
                "solution_level": solution_level,
            }
        )
        payload["loop_control"] = loop_control
        if resume_current_batch:
            continuation_source = "run_local_overlay" if str(proposal_payload.get("proposal_id") or "") else "applied_solution"
            payload["status"] = "running"
            return {
                **current,
                "status": "running",
                "reason_tag": f"item_loop_resume_with_{continuation_source}",
                container_key: payload,
                "continuation": {
                    "kind": "item_loop",
                    "phase": "apply",
                    "resume_from": "next_item",
                    "source": continuation_source,
                    "solution_run_id": solution_run_id,
                    "active_run_local_proposal_id": str(proposal_payload.get("proposal_id") or ""),
                    "active_run_local_proposal_memory_id": str(proposal.get("memory_id") or ""),
                },
                "message": "Resuming item loop with an applied evolution proposal.",
            }
        if solution_level == SOLUTION_LEVEL_OUTER_SYNTHESIS:
            payload["status"] = "blocked"
            updated = {
                **current,
                "status": "blocked",
                "reason_tag": "outer_synthesis_solution_applied",
                container_key: payload,
                "message": "Outer synthesis proposal was applied; the current batch should close and a follow-up batch should validate it.",
            }
            updated.pop("continuation", None)
            return updated
        payload["status"] = str(payload.get("status") or "blocked")
        updated = {**current, container_key: payload}
        updated.pop("continuation", None)
        return updated

    @staticmethod
    def _drop_site_keys(*, batch: dict[str, Any], site_key: str, keys: tuple[str, ...]) -> dict[str, Any]:
        updated_batch = dict(batch or {})
        sites = dict(updated_batch.get("sites") or {})
        row = sites.get(site_key)
        if not isinstance(row, dict):
            return updated_batch
        updated_row = dict(row)
        for key in keys:
            updated_row.pop(key, None)
        sites[site_key] = updated_row
        updated_batch["sites"] = sites
        return updated_batch

    @classmethod
    def _solution_level(cls, row: dict[str, Any]) -> str:
        _container_key, container = cls._evolution_container(row)
        loop_control = container.get("loop_control") if isinstance(container.get("loop_control"), dict) else {}
        if str(loop_control.get("solution_request_kind") or "") == "synthesis_work_order" or bool(
            loop_control.get("synthesis_required")
        ):
            return SOLUTION_LEVEL_OUTER_SYNTHESIS
        if cls._site_is_waiting_solution(row) or cls._solution_run_id(row):
            return SOLUTION_LEVEL_ITEM_LOOP
        return ""

    def _site_has_stopped_evolution_evidence(self, row: dict[str, Any]) -> bool:
        if self._site_is_waiting_solution(row):
            return False
        site_status = str(row.get("status") or "").strip()
        container_key, container = self._evolution_container(row)
        container_status = str(container.get("status") or "").strip()
        if site_status in {"queued", "running", "ready"} or container_status in {"queued", "running", "ready"}:
            return False
        if site_status not in {"blocked", "failed"} and container_status not in {"blocked", "failed"}:
            return False
        loop_payload = container.get("loop_control") if isinstance(container.get("loop_control"), dict) else {}
        if not loop_payload:
            return False
        if str(loop_payload.get("solution_request_kind") or "") == "synthesis_work_order":
            return False
        artifacts = loop_payload.get("artifacts") if isinstance(loop_payload.get("artifacts"), dict) else {}
        if not str(artifacts.get("action_card_id") or "").strip():
            return False
        control = loop_control_from_row(loop_payload)
        if not control or loop_control_is_human_only_gap(control):
            return False
        if container_key == "evolution":
            transition = loop_payload.get("item_loop_transition") if isinstance(loop_payload.get("item_loop_transition"), dict) else {}
            return bool(loop_payload.get("artifacts", {}).get("escalated")) or str(transition.get("action") or "") == "pause_threshold"
        return True

    @classmethod
    def _site_requires_exploration_synthesis(cls, row: dict[str, Any]) -> bool:
        """Recognize terminal exploration structurally, without judging its result."""

        if cls._site_is_waiting_solution(row):
            return False
        if str(row.get("status") or "").strip() not in {"completed", "partial_completed", "failed", "blocked"}:
            return False
        scope = row.get("evolution_scope") if isinstance(row.get("evolution_scope"), dict) else {}
        execution_mode = str(
            scope.get("execution_mode") or row.get("execution_mode") or row.get("site_mode") or ""
        ).strip()
        if execution_mode != "exploration" or not bool(scope.get("active", execution_mode == "exploration")):
            return False
        _container_key, container = cls._evolution_container(row)
        loop_control = container.get("loop_control") if isinstance(container.get("loop_control"), dict) else {}
        return not bool(loop_control.get("synthesis_required"))

    @classmethod
    def _synthesis_waiting_solution_site_row(cls, *, current: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        container_key, container = cls._evolution_container(current)
        payload = dict(container or {})
        loop_control = dict(payload.get("loop_control") or {})
        loop_control.update(
            {
                "waiting_solution": True,
                "solution_request_kind": "synthesis_work_order",
                "synthesis_required": True,
                "solution_run_id": str(request.get("run_id") or ""),
                "solution_request": str(request.get("solution_request") or ""),
                "proposal_output_path": str(request.get("proposal_output_path") or ""),
                "evidence_pack": str(request.get("evidence_pack") or ""),
            }
        )
        payload["loop_control"] = loop_control
        payload["status"] = "waiting_solution"
        return {
            **current,
            "status": "waiting_solution",
            "reason_tag": "evolution_synthesis_waiting_solution",
            "solution_run_id": str(request.get("run_id") or ""),
            "solution_request": str(request.get("solution_request") or ""),
            "proposal_output_path": str(request.get("proposal_output_path") or ""),
            container_key: payload,
            "message": "Evolution synthesis work order created from stopped execution-unit evidence.",
        }

    @staticmethod
    def _threshold_site_row(
        *,
        current: dict[str, Any],
        attempts: int,
        limit: int,
        phase: str,
        pattern: str,
    ) -> dict[str, Any]:
        apply_payload = dict(current.get("apply") or {})
        loop_control = dict(apply_payload.get("loop_control") or {})
        loop_control.update(
            {
                "waiting_solution": False,
                "should_pause": True,
                "threshold_reached": True,
                "attempts": int(attempts or 0),
                "max_attempts": int(limit or 0),
                "item_loop_transition": {
                    "action": "pause_threshold",
                    "hold_next_item": True,
                    "pause_loop": True,
                    "requires_materialized_change": False,
                    "should_create_solution_request": False,
                    "reason_tag": "item_loop_refinement_threshold",
                    "message": "Refinement threshold reached for this loop pattern.",
                },
            }
        )
        apply_payload["loop_control"] = loop_control
        apply_payload["status"] = "blocked"
        return {
            **current,
            "status": "blocked",
            "reason_tag": "item_loop_refinement_threshold",
            "current_phase": phase,
            "solution_run_id": "",
            "solution_request": "",
            "proposal_output_path": "",
            "apply": apply_payload,
            "message": (
                f"Item loop threshold reached for `{pattern}` after {int(attempts or 0)} "
                f"attempt(s); no additional run-local proposal will be requested for this item loop."
            ),
        }

    @staticmethod
    def _item_loop_continuation(row: dict[str, Any]) -> dict[str, Any]:
        continuation = row.get("continuation") if isinstance(row.get("continuation"), dict) else {}
        if continuation:
            return continuation
        if str(row.get("reason_tag") or "").startswith("item_loop_resume_with_"):
            return {"kind": "item_loop", "phase": "apply", "resume_from": "next_item"}
        return {}

    @classmethod
    def _site_has_item_loop_continuation(cls, row: dict[str, Any]) -> bool:
        continuation = cls._item_loop_continuation(row)
        if str(continuation.get("kind") or "") != "item_loop":
            return False
        if str(continuation.get("resume_from") or "") not in {"next_item", "current_item", "nearest"}:
            return False
        return str(row.get("status") or "") in {"running", "ready"}

    @classmethod
    def _batch_has_item_loop_continuation(cls, batch: dict[str, Any]) -> bool:
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        return any(isinstance(row, dict) and cls._site_has_item_loop_continuation(row) for row in sites.values())

    @staticmethod
    def _site_is_waiting_solution(row: dict[str, Any]) -> bool:
        _container_key, container = BatchEvolutionOrchestrator._evolution_container(row)
        return str(row.get("status") or "") == "waiting_solution" or str(container.get("status") or "") == "waiting_solution"

    @classmethod
    def _waiting_solution_run_ids(cls, batch: dict[str, Any]) -> list[str]:
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        run_ids: list[str] = []
        seen: set[str] = set()
        for row in sites.values():
            if not isinstance(row, dict) or not cls._site_is_waiting_solution(row):
                continue
            run_id = cls._solution_run_id(row)
            if not run_id or run_id in seen:
                continue
            run_ids.append(run_id)
            seen.add(run_id)
        return run_ids

    @staticmethod
    def _solution_run_id(row: dict[str, Any]) -> str:
        _container_key, container = BatchEvolutionOrchestrator._evolution_container(row)
        loop_control = container.get("loop_control") if isinstance(container.get("loop_control"), dict) else {}
        return str(loop_control.get("solution_run_id") or row.get("solution_run_id") or "").strip()

    @staticmethod
    def _evolution_container(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Prefer generic phase evolution state; retain apply compatibility."""

        evolution = row.get("evolution") if isinstance(row.get("evolution"), dict) else {}
        if isinstance(evolution.get("loop_control"), dict):
            return "evolution", evolution
        apply_payload = row.get("apply") if isinstance(row.get("apply"), dict) else {}
        return "apply", apply_payload

    def _solution_run_is_applied(self, run_id: str) -> bool:
        normalized = str(run_id or "").strip()
        if not normalized:
            return False
        payload = read_json(Path(self.job_store.workspace) / "evolution" / "runs" / normalized / "run.json")
        return str(payload.get("status") or "") == "applied"
