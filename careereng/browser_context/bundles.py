"""Bundle sessions for browser-phase context injection."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from careereng.browser_context.phase_memory import BrowserPhaseMemory
from careereng.browser_context.registry import BrowserContextRegistry
from careereng.resume.export import default_apply_resume_pdf_path


def _target_job_ids(target_job_ids: tuple[str, ...] | None = None) -> set[str]:
    return {
        str(job_id or "").strip()
        for job_id in (target_job_ids or ())
        if str(job_id or "").strip()
    }


def _pending_apply_rows(
    *,
    site_store: Any,
    site_key: str,
    batch_id: str,
    target_job_ids: tuple[str, ...] | None = None,
    include_target_terminal: bool = False,
) -> list[dict[str, Any]]:
    target_ids = _target_job_ids(target_job_ids)
    list_run_jobs = getattr(site_store, "list_run_jobs", None)
    if callable(list_run_jobs):
        try:
            run_rows = list_run_jobs(site_key, batch_id)
        except Exception:
            run_rows = []
    else:
        run_rows = []
    terminal_decisions = {"filtered_out", "already_applied"}
    terminal_applications = {"already_applied", "filtered_out", "submitted", "apply_failed", "blocked"}
    pending_rows: list[dict[str, Any]] = []
    for row in run_rows:
        if not isinstance(row, dict):
            continue
        job_id = str(row.get("job_id") or "").strip()
        if target_ids and job_id not in target_ids:
            continue
        decision_status = str(row.get("decision_status") or "").strip().lower()
        application_status = str(row.get("application_status") or "").strip().lower()
        target_terminal_allowed = bool(include_target_terminal and target_ids and job_id in target_ids)
        if not target_terminal_allowed and (
            decision_status in terminal_decisions or application_status in terminal_applications
        ):
            continue
        pending_rows.append(
            {
                "job_id": job_id,
                "title": str(row.get("title") or ""),
                "url": str(row.get("url") or ""),
                "location": str(row.get("location") or ""),
                "posted_label": str(row.get("posted_label") or ""),
                "employment_type": str(row.get("employment_type") or ""),
                "match_label": str(row.get("match_label") or ""),
                "apply_state": str(row.get("apply_state") or ""),
                "decision_status": str(row.get("decision_status") or ""),
                "application_status": str(row.get("application_status") or ""),
                "application_status_raw": str(row.get("application_status_raw") or ""),
            }
        )
    return pending_rows


def _nvidia_apply_batch_fact(
    *,
    site_store: Any,
    site_key: str,
    batch_id: str,
    target_job_ids: tuple[str, ...] | None = None,
) -> str:
    if str(site_key or "").strip().lower() != "nvidia":
        return ""
    target_ids = _target_job_ids(target_job_ids)
    if not target_ids:
        return ""
    list_run_jobs = getattr(site_store, "list_run_jobs", None)
    if not callable(list_run_jobs):
        return ""
    try:
        run_rows = list_run_jobs(site_key, batch_id)
    except Exception:
        run_rows = []

    prior_apply_statuses = {
        "in_progress",
        "signin_required",
        "submitted",
        "apply_failed",
        "blocked",
    }
    prior_apply_seen = False
    for row in run_rows:
        if not isinstance(row, dict):
            continue
        job_id = str(row.get("job_id") or "").strip()
        if not job_id or job_id in target_ids:
            continue
        decision_status = str(row.get("decision_status") or "").strip().lower()
        application_status = str(row.get("application_status") or "").strip().lower()
        if decision_status in {"filtered_out", "already_applied"}:
            continue
        if application_status == "already_applied":
            continue
        if application_status in prior_apply_statuses:
            prior_apply_seen = True
            break

    if prior_apply_seen:
        return (
            "Current NVIDIA batch apply fact: at least one earlier NVIDIA job in this same batch has already gone "
            "through the normal apply flow. If the live page now offers `Use My Last Application`, prefer that "
            "reuse path before falling back to the normal visible application entry."
        )
    return (
        "Current NVIDIA batch apply fact: this is the first NVIDIA job being applied in this current batch. "
        "Stay with the normal live-page resume upload / autofill path for this one."
    )


@dataclass
class BrowserContextSession:
    bundle_texts: dict[str, str]
    base_items: list[dict[str, str]]
    phase_memory: BrowserPhaseMemory | None = None
    _attached_bundles: list[str] = field(default_factory=list)

    @classmethod
    def for_apply(
        cls,
        *,
        registry: BrowserContextRegistry,
        workspace: Path,
        site_store: Any,
        site_key: str,
        batch_id: str,
        target_job_ids: tuple[str, ...] | None = None,
        staged_resume_pdf_path: str = "",
        phase_memory: BrowserPhaseMemory | None = None,
        continuation_context: dict[str, Any] | None = None,
    ) -> "BrowserContextSession":
        bundle_texts = {name: registry.bundle_item_text(name) for name in registry.available_bundles()}
        load_run_context = getattr(site_store, "load_run_context", None)
        if callable(load_run_context):
            try:
                run_context = load_run_context(site_key, batch_id)
            except Exception:
                run_context = {}
        else:
            run_context = {}
        apply_carry_forward = str(run_context.get("apply_carry_forward") or "").strip() if isinstance(run_context, dict) else ""
        apply_loop_refinement_summary = (
            str(run_context.get("apply_loop_refinement_summary") or "").strip()
            if isinstance(run_context, dict)
            else ""
        )
        try:
            from careereng.evolution.memory_units import (
                EvolutionMemoryStore,
                evolution_memory_has_materialized_change,
                render_evolution_memory_guidance,
            )

            evolution_units = EvolutionMemoryStore(workspace).query(
                scopes=[f"batch:{batch_id}:site:{site_key}:apply"],
                phase="apply",
                lifecycles=["run_local"],
                statuses=["active"],
                limit=20,
            )
            materialized_units = [unit for unit in evolution_units if evolution_memory_has_materialized_change(unit)]
            evolution_memory_summary = render_evolution_memory_guidance(
                materialized_units[-1:],
                title="Current Batch Active Run-Local Proposal",
            )
        except Exception:
            evolution_memory_summary = ""
        pending_rows = _pending_apply_rows(
            site_store=site_store,
            site_key=site_key,
            batch_id=batch_id,
            target_job_ids=target_job_ids,
            include_target_terminal=bool(continuation_context),
        )
        items: list[dict[str, str]] = []
        continuation_item = cls._continuation_context_item(continuation_context)
        if continuation_item:
            items.append(continuation_item)
        items.append(
            {
                "role": "user",
                "content": (
                    (
                        "Current apply target for this site and batch. Work only on this job:\n"
                        if len({str(job_id or '').strip() for job_id in (target_job_ids or ()) if str(job_id or '').strip()}) == 1
                        else "Current apply targets for this site and batch:\n"
                    )
                    + json.dumps(pending_rows, ensure_ascii=False)
                ),
            }
        )
        resume_pdf_path = str(staged_resume_pdf_path or "").strip() or str(default_apply_resume_pdf_path(workspace))
        items.append(
            {
                "role": "user",
                "content": (
                    "Run-local staged resume PDF for this apply phase "
                    f"(use this exact local path if upload is needed): {resume_pdf_path}"
                ),
            }
        )
        resume_file_name = Path(resume_pdf_path).name.strip()
        if resume_file_name:
            items.append(
                {
                    "role": "user",
                    "content": (
                        "Current staged resume filename for this apply phase "
                        f"(compare the live page's selected resume name against this basename): {resume_file_name}"
                    ),
                }
            )
        nvidia_batch_fact = _nvidia_apply_batch_fact(
            site_store=site_store,
            site_key=site_key,
            batch_id=batch_id,
            target_job_ids=target_job_ids,
        )
        if nvidia_batch_fact:
            items.append({"role": "user", "content": nvidia_batch_fact})
        if apply_carry_forward:
            items.append(
                {
                    "role": "user",
                    "content": (
                        "Current apply carry-forward from an earlier completed job in this same site batch:\n"
                        f"{apply_carry_forward}"
                    ),
                }
            )
        if apply_loop_refinement_summary:
            items.append(
                {
                    "role": "user",
                    "content": (
                        "Current apply-loop refinement guidance from earlier apply targets in this same batch:\n"
                        f"{apply_loop_refinement_summary}\n"
                        "Treat this as temporary strategy guidance for the next target; it does not override confirmed site/project Skills."
                    ),
                }
            )
        if evolution_memory_summary:
            items.append(
                {
                    "role": "user",
                    "content": (
                        "Active run-local item-loop proposal(s) for this apply phase:\n"
                        f"{evolution_memory_summary}\n"
                        "Treat this latest materialized run-local overlay as the strategy currently under validation for this apply item. "
                        "Do not use older or site-scope run-local proposal history as execution instructions."
                    ),
                }
            )
        if "apply_facts" in bundle_texts:
            items.append({"role": "user", "content": bundle_texts["apply_facts"]})
        items.append({"role": "user", "content": registry.available_bundles_item_text()})
        return cls(bundle_texts=bundle_texts, base_items=items, phase_memory=phase_memory)

    @classmethod
    def for_phase(
        cls,
        *,
        phase_memory: BrowserPhaseMemory | None = None,
        continuation_context: dict[str, Any] | None = None,
    ) -> "BrowserContextSession":
        items: list[dict[str, str]] = []
        continuation_item = cls._continuation_context_item(continuation_context)
        if continuation_item:
            items.append(continuation_item)
        return cls(bundle_texts={}, base_items=items, phase_memory=phase_memory)

    @staticmethod
    def _continuation_context_item(continuation_context: dict[str, Any] | None) -> dict[str, str] | None:
        if not isinstance(continuation_context, dict) or not continuation_context:
            return None
        return {
            "role": "user",
            "content": (
                "Fresh snapshot resume context for this phase:\n"
                f"{json.dumps(continuation_context, ensure_ascii=False, sort_keys=True)}\n"
                "The user has completed the external/manual step. Take a fresh live snapshot of the current page, "
                "then use the current live page plus Skills to decide the next action. "
                "This context is not a business outcome; terminal job/application updates must still come from the live page."
            ),
        }

    def items(self) -> list[dict[str, str]]:
        items = list(self.base_items)
        for bundle in self._attached_bundles:
            text = self.bundle_texts.get(bundle, "")
            if text:
                items.append({"role": "user", "content": text})
        return items

    def request_bundle(self, *, bundle: str, reason: str = "") -> dict[str, Any]:
        normalized = str(bundle or "").strip().lower()
        reason_text = str(reason or "").strip()
        available_bundles = sorted(self.bundle_texts.keys())
        if not normalized:
            return {
                "isError": False,
                "structuredContent": {
                    "bundle": "",
                    "available": False,
                    "status": "missing_bundle_name",
                    "available_bundles": available_bundles,
                    "reason": reason_text,
                },
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "### Result\n"
                            "- request_context requires a non-empty bundle name.\n"
                            f"- Available bundles: {', '.join(available_bundles) or '(none)'}"
                        ),
                    }
                ],
            }
        if normalized not in self.bundle_texts:
            return {
                "isError": False,
                "structuredContent": {
                    "bundle": normalized,
                    "available": False,
                    "status": "unknown_bundle",
                    "available_bundles": available_bundles,
                    "reason": reason_text,
                },
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "### Result\n"
                            f"- Context bundle `{normalized}` is not available for this run.\n"
                            f"- Available bundles: {', '.join(available_bundles) or '(none)'}"
                        ),
                    }
                ],
            }
        if normalized not in self._attached_bundles:
            self._attached_bundles.append(normalized)
            status = "attached"
            detail = "will be included in subsequent turns."
        else:
            status = "already_attached"
            detail = "is already attached for subsequent turns."
        return {
            "isError": False,
            "structuredContent": {
                "bundle": normalized,
                "available": True,
                "status": status,
                "reason": reason_text,
            },
            "content": [
                {
                    "type": "text",
                    "text": (
                        "### Result\n"
                        f"- Context bundle `{normalized}` {detail}\n"
                        + (f"- Reason: {reason_text}" if reason_text else "")
                    ),
                }
            ],
        }
