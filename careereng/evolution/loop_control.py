"""Reusable loop-control helpers for workflow evolution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from careereng.evolution.artifacts import EvolutionEvidenceStore, OpenEvolutionCandidateStore
from careereng.evolution.work_items import ActionCardStore
from careereng.evolution.work_items.schema import ACTION_CARD_CODEX_REVIEW, ACTION_CARD_HUMAN_ACTION
from careereng.evolution.proposals import SUPPORTED_CHANGE_TYPES
from careereng.evolution.schema import EvolutionEvidence, ImprovementCandidate
from careereng.utils import now_iso, safe_file_stem


LOOP_ACTION_CONTINUE = "continue"
LOOP_ACTION_RETRY_RECOVERY = "retry_recovery"
LOOP_ACTION_TRIGGER_REFINEMENT = "trigger_refinement"
LOOP_ACTION_REQUEST_USER_INPUT = "request_user_input"
LOOP_ACTION_PAUSE_SITE = "pause_site"
LOOP_ACTION_PAUSE_BATCH = "pause_batch"

LOOP_CONTROL_ACTIONS = {
    LOOP_ACTION_RETRY_RECOVERY,
    LOOP_ACTION_TRIGGER_REFINEMENT,
    LOOP_ACTION_REQUEST_USER_INPUT,
    LOOP_ACTION_PAUSE_SITE,
    LOOP_ACTION_PAUSE_BATCH,
}

LOOP_PAUSE_ACTIONS = {
    LOOP_ACTION_PAUSE_SITE,
    LOOP_ACTION_PAUSE_BATCH,
}

LOOP_AUTO_REFINEMENT_ACTIONS = {
    LOOP_ACTION_TRIGGER_REFINEMENT,
}

LOOP_USER_INPUT_ACTIONS = {
    LOOP_ACTION_REQUEST_USER_INPUT,
}

LOOP_CANDIDATE_SPEC_BY_PHASE = {
    "apply": "apply_form_workflow",
}

EVOLUTION_DECISION_CONTINUE = "continue_evolution"
EVOLUTION_DECISION_NEEDS_USER_INPUT = "needs_user_input"
EVOLUTION_DECISION_NEEDS_SOLUTION = "needs_solution_proposal"
EVOLUTION_DECISION_STOP_NO_ACTION = "stop_no_action"

EVOLUTION_DECISION_VERDICTS = {
    EVOLUTION_DECISION_CONTINUE,
    EVOLUTION_DECISION_NEEDS_SOLUTION,
    EVOLUTION_DECISION_NEEDS_USER_INPUT,
    EVOLUTION_DECISION_STOP_NO_ACTION,
}

HUMAN_ONLY_GAP_TYPES = {
    "auth_required",
    "captcha",
    "captcha_required",
    "mfa_required",
    "verification_required",
    "human_only",
    "human-only",
    "login_required",
    "password_required",
}


def normalize_loop_control_action(value: Any) -> str:
    text = str(value or "").strip().lower()
    # `continue` is normal workflow control, not loop-control/evolution evidence.
    if text == LOOP_ACTION_CONTINUE:
        return ""
    return text if text in LOOP_CONTROL_ACTIONS else ""


def loop_control_from_row(row: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(row, dict):
        return {}
    action = normalize_loop_control_action(row.get("loop_control_action") or row.get("recommended_action") or row.get("action"))
    if not action:
        return {}
    pattern = str(row.get("failure_pattern") or "").strip()
    reason_type = str(row.get("block_reason_type") or row.get("gap_type") or "").strip()
    if not pattern:
        pattern = reason_type or "unknown_loop_pattern"
    target = str(row.get("recommended_target") or row.get("target") or "").strip()
    current_item_ref = str(row.get("current_item_ref") or row.get("url") or row.get("job_id") or "").strip()
    return {
        "action": action,
        "loop_scope": str(row.get("loop_scope") or "").strip(),
        "block_reason_type": reason_type,
        "gap_type": reason_type,
        "failure_pattern": safe_file_stem(pattern).replace("-", "_"),
        "recommended_target": target,
        "target": target,
        "resume_policy": str(row.get("resume_policy") or "").strip(),
        "current_item_ref": current_item_ref,
        "evidence": str(row.get("evidence") or "").strip(),
        "refinement_hint": str(row.get("refinement_hint") or "").strip(),
    }


def should_pause_loop(row: dict[str, Any] | None) -> bool:
    action = loop_control_from_row(row).get("action", "")
    return action in LOOP_PAUSE_ACTIONS


def loop_action_requires_refinement(row: dict[str, Any] | None) -> bool:
    return loop_control_from_row(row).get("action") == LOOP_ACTION_TRIGGER_REFINEMENT


def loop_action_requires_user_input(row: dict[str, Any] | None) -> bool:
    return loop_control_from_row(row).get("action") == LOOP_ACTION_REQUEST_USER_INPUT


def loop_control_is_human_only_gap(control: dict[str, Any] | None) -> bool:
    if not isinstance(control, dict):
        return False
    gap_type = str(control.get("gap_type") or control.get("block_reason_type") or "").strip().lower()
    return gap_type in HUMAN_ONLY_GAP_TYPES


def build_evolution_decision(
    *,
    site_key: str,
    phase: str,
    batch_id: str,
    control: dict[str, Any] | None,
    artifacts: dict[str, Any] | None = None,
    attempt: int = 1,
    max_attempts: int = 1,
    previous_batch_id: str = "",
) -> dict[str, Any]:
    """Build a thin outer-loop decision from LLM-provided loop evidence.

    This does not classify business cases. The LLM/Skill supplies the action,
    target, evidence, and hint; Python only turns that into a durable loop
    contract that the next batch can carry.
    """

    normalized_control = loop_control_from_row(control or {})
    action = str(normalized_control.get("action") or "").strip()
    if not action:
        return {}
    artifact_payload = artifacts if isinstance(artifacts, dict) else {}
    needs_user_input = bool(
        action == LOOP_ACTION_REQUEST_USER_INPUT or loop_control_is_human_only_gap(normalized_control)
    )
    target_ref = str(
        artifact_payload.get("target_ref")
        or normalized_control.get("target")
        or normalized_control.get("recommended_target")
        or ""
    ).strip()
    failure_pattern = str(normalized_control.get("failure_pattern") or "unknown_loop_pattern").strip()
    if needs_user_input:
        verdict = EVOLUTION_DECISION_NEEDS_USER_INPUT
    elif action == LOOP_ACTION_TRIGGER_REFINEMENT:
        verdict = EVOLUTION_DECISION_NEEDS_SOLUTION
    else:
        verdict = EVOLUTION_DECISION_STOP_NO_ACTION
    evidence_id = str(artifact_payload.get("evidence_id") or "").strip()
    action_card = str(artifact_payload.get("action_card") or "").strip()
    candidate_id = str(artifact_payload.get("candidate_id") or "").strip()
    fingerprint_payload = {
        "site_key": safe_file_stem(site_key),
        "phase": str(phase or "").strip(),
        "batch_id": str(batch_id or "").strip(),
        "action": action,
        "pattern": failure_pattern,
        "evidence_id": evidence_id,
        "target_ref": target_ref,
    }
    decision_id = f"evo_decision_{_fingerprint(fingerprint_payload)[:16]}"
    hint = str(normalized_control.get("refinement_hint") or "").strip()
    evidence = str(normalized_control.get("evidence") or "").strip()
    overlay_lines = [
        f"Evolution decision `{decision_id}` is active.",
        f"Verdict: `{verdict}`.",
        f"Action: `{action}`.",
        f"Failure pattern: `{failure_pattern}`.",
    ]
    if target_ref:
        overlay_lines.append(f"Target: `{target_ref}`.")
    if hint:
        overlay_lines.append(f"Refinement request: {hint}")
    if evidence:
        overlay_lines.append(f"Evidence: {evidence}")
    if action_card:
        overlay_lines.append(f"Action card: {action_card}")
    return {
        "decision_id": decision_id,
        "created_at": now_iso(),
        "verdict": verdict,
        "needs_user_input": needs_user_input,
        "site_key": safe_file_stem(site_key),
        "phase": str(phase or "").strip(),
        "batch_id": str(batch_id or "").strip(),
        "previous_batch_id": str(previous_batch_id or "").strip(),
        "attempt": max(1, int(attempt or 1)),
        "max_attempts": max(1, int(max_attempts or 1)),
        "loop_control_action": action,
        "failure_pattern": failure_pattern,
        "gap_type": str(normalized_control.get("gap_type") or normalized_control.get("block_reason_type") or "").strip(),
        "target_ref": target_ref,
        "recommended_target": str(normalized_control.get("recommended_target") or "").strip(),
        "resume_policy": str(normalized_control.get("resume_policy") or "").strip(),
        "evidence": evidence,
        "refinement_hint": hint,
        "requires_solution_provider": verdict == EVOLUTION_DECISION_NEEDS_SOLUTION,
        "proposal_status": "needs_solution_proposal" if verdict == EVOLUTION_DECISION_NEEDS_SOLUTION else "",
        "materialized_change": False,
        "proposal_overlay": "\n".join(overlay_lines).strip(),
        "next_batch_strategy": (
            "pause_for_solution_provider"
            if verdict == EVOLUTION_DECISION_NEEDS_SOLUTION
            else "pause_for_user_or_no_action"
        ),
        "validation_plan": (
            "Do not continue the item loop or start a follow-up batch until an assistant solution provider "
            "writes and applies a concrete proposal."
            if verdict == EVOLUTION_DECISION_NEEDS_SOLUTION
            else (
                f"The follow-up batch should not repeat `{failure_pattern}` unchanged; it should either reach a "
                "terminal job state, produce a new structured loop-control gap, or prove that user input is required."
            )
        ),
        "source": {
            "evidence_id": evidence_id,
            "candidate_id": candidate_id,
            "action_card": action_card,
        },
    }


def create_loop_control_artifacts(
    *,
    workspace: Path | str,
    project_root: Path | str,
    site_key: str,
    site_name: str,
    phase: str,
    batch_id: str,
    job_row: dict[str, Any],
    per_batch_attempts: int = 1,
    max_refinement_attempts_per_batch: int = 5,
    max_failed_batches_per_pattern: int = 3,
) -> dict[str, Any]:
    """Persist evidence/candidate/card for a loop-control pause.

    The helper is intentionally generic. It does not decide business meaning; it
    records the LLM-provided loop-control classification and creates the next
    review/refinement surface for Codex or a human.
    """

    workspace_path = Path(workspace)
    root = Path(project_root)
    control = loop_control_from_row(job_row)
    action = control.get("action", "")
    if not action:
        return {}

    normalized_site = safe_file_stem(site_key)
    normalized_phase = safe_file_stem(phase)
    pattern = control.get("failure_pattern") or "unknown_loop_pattern"
    now = now_iso()
    target_ref = _recommended_target_ref(
        recommended_target=control.get("recommended_target", ""),
        project_root=root,
        site_key=normalized_site,
    )
    fingerprint_payload = {
        "area": "loop_engineering",
        "site_key": normalized_site,
        "phase": normalized_phase,
        "pattern": pattern,
        "action": action,
        "target_ref": target_ref,
    }
    evidence_id = _stable_id("evidence", {**fingerprint_payload, "batch_id": batch_id, "job_id": job_row.get("job_id")})
    candidate_id = _stable_id("candidate", fingerprint_payload)
    previous_failed_batches = _previous_failed_batch_count(
        workspace=workspace_path,
        site_key=normalized_site,
        phase=normalized_phase,
        pattern=pattern,
        current_batch_id=batch_id,
    )
    max_failed = max(0, int(max_failed_batches_per_pattern or 0))
    max_per_batch = max(0, int(max_refinement_attempts_per_batch or 0))
    attempts_in_batch = max(0, int(per_batch_attempts or 0))
    batch_attempts_exhausted = bool(action == LOOP_ACTION_TRIGGER_REFINEMENT and max_per_batch and attempts_in_batch >= max_per_batch)
    history_failed_batches_exhausted = bool(max_failed and previous_failed_batches >= max_failed)
    # Cross-batch repetition is long-term evolution evidence. It should raise
    # priority, but it must not stop the current batch before the in-batch loop
    # has had a chance to apply the latest run-local guidance.
    escalated = bool(batch_attempts_exhausted)
    severity = "high" if escalated or history_failed_batches_exhausted or action in {LOOP_ACTION_REQUEST_USER_INPUT, LOOP_ACTION_PAUSE_BATCH} else "medium"
    summary = _summary_for_control(site_name=site_name or normalized_site, phase=normalized_phase, pattern=pattern, action=action)

    evidence = EvolutionEvidence(
        evidence_id=evidence_id,
        created_at=now,
        source_type="loop_control",
        source_ref=f"workspace/sites/{normalized_site}/jobs/runs/{batch_id}.jsonl",
        area="loop_engineering",
        site_key=normalized_site,
        phase=normalized_phase,
        event_type=action,
        severity=severity,
        summary=summary,
        details={
            "batch_id": batch_id,
            "job_id": str(job_row.get("job_id") or ""),
            "title": str(job_row.get("title") or ""),
            "url": str(job_row.get("url") or ""),
            "application_status": str(job_row.get("application_status") or ""),
            "application_status_raw": str(job_row.get("application_status_raw") or ""),
            "last_apply_error": str(job_row.get("last_apply_error") or ""),
            "loop_scope": control.get("loop_scope", ""),
            "block_reason_type": control.get("block_reason_type", ""),
            "gap_type": control.get("gap_type", ""),
            "failure_pattern": pattern,
            "loop_control_action": action,
            "recommended_action": action,
            "recommended_target": control.get("recommended_target", ""),
            "target": control.get("target", ""),
            "resume_policy": control.get("resume_policy", ""),
            "current_item_ref": control.get("current_item_ref", ""),
            "target_ref": target_ref,
            "evidence": control.get("evidence", ""),
            "refinement_hint": control.get("refinement_hint", ""),
            "previous_failed_batches": previous_failed_batches,
            "max_failed_batches_per_pattern": max_failed,
            "per_batch_attempts": attempts_in_batch,
            "max_refinement_attempts_per_batch": max_per_batch,
            "batch_attempts_exhausted": batch_attempts_exhausted,
            "history_failed_batches_exhausted": history_failed_batches_exhausted,
            "escalated": escalated,
            "recent_tool_chain": _string_list(job_row.get("_loop_recent_tool_chain")),
            "last_tool_outputs": _string_list(job_row.get("_loop_last_tool_outputs")),
            "next_iteration_guidance": str(job_row.get("_loop_next_iteration_guidance") or ""),
            "candidate_spec_id": _candidate_spec_for(phase=normalized_phase, action=action),
        },
        entities={
            "site_key": normalized_site,
            "phase": normalized_phase,
            "failure_pattern": pattern,
            "gap_type": control.get("gap_type", ""),
            "target_ref": target_ref,
            "resume_policy": control.get("resume_policy", ""),
        },
        tags=["loop_engineering", normalized_phase, normalized_site, pattern, action],
        fingerprint=_fingerprint(fingerprint_payload),
    ).to_dict()

    candidate = ImprovementCandidate(
        candidate_id=candidate_id,
        created_at=now,
        updated_at=now,
        area="loop_engineering",
        target_type="ai_skill" if target_ref.endswith((".md", ".markdown")) else "workflow_context",
        target_ref=target_ref,
        site_key=normalized_site,
        phase=normalized_phase,
        priority="high" if escalated or history_failed_batches_exhausted else ("high" if action == LOOP_ACTION_REQUEST_USER_INPUT else "medium"),
        status="open",
        summary=summary,
        suggested_change=control.get("refinement_hint", "") or _default_suggested_change(action),
        reason=control.get("evidence", "") or str(job_row.get("last_apply_error") or ""),
        evidence_ids=[evidence_id],
        evidence_count=1,
        risk="medium",
        owner="human" if escalated or action == LOOP_ACTION_REQUEST_USER_INPUT else "codex",
        fingerprint=_fingerprint(fingerprint_payload),
    ).to_dict()

    evidence_store = EvolutionEvidenceStore(workspace_path)
    candidate_store = OpenEvolutionCandidateStore(workspace_path)
    evidence_store.upsert_many([evidence])
    if action == LOOP_ACTION_TRIGGER_REFINEMENT:
        candidate = _merge_existing_candidate_evidence(store=candidate_store, candidate=candidate, evidence_id=evidence_id)
        candidate_store.upsert_many([candidate])

    card = _create_action_card(
        workspace=workspace_path,
        site_key=normalized_site,
        phase=normalized_phase,
        batch_id=batch_id,
        action=action,
        pattern=pattern,
        target_ref=target_ref,
        summary=summary,
        evidence=evidence,
        candidate=candidate if action == LOOP_ACTION_TRIGGER_REFINEMENT else {},
        escalated=escalated,
    )
    return {
        "action": action,
        "failure_pattern": pattern,
        "block_reason_type": control.get("block_reason_type", ""),
        "gap_type": control.get("gap_type", ""),
        "evidence_id": evidence_id,
        "candidate_id": candidate_id if action == LOOP_ACTION_TRIGGER_REFINEMENT else "",
        "action_card_id": str(card.get("card_id") or "") if isinstance(card, dict) else "",
        "action_card": str(card.get("markdown_path") or "") if isinstance(card, dict) else "",
        "target_ref": target_ref,
        "target": control.get("target", ""),
        "recommended_target": control.get("recommended_target", ""),
        "resume_policy": control.get("resume_policy", ""),
        "evidence": control.get("evidence", ""),
        "refinement_hint": control.get("refinement_hint", ""),
        "escalated": escalated,
        "previous_failed_batches": previous_failed_batches,
        "history_failed_batches_exhausted": history_failed_batches_exhausted,
        "per_batch_attempts": attempts_in_batch,
        "max_refinement_attempts_per_batch": max_per_batch,
    }


def _create_action_card(
    *,
    workspace: Path,
    site_key: str,
    phase: str,
    batch_id: str,
    action: str,
    pattern: str,
    target_ref: str,
    summary: str,
    evidence: dict[str, Any],
    candidate: dict[str, Any],
    escalated: bool,
) -> dict[str, Any]:
    card_type = ACTION_CARD_HUMAN_ACTION if action == LOOP_ACTION_REQUEST_USER_INPUT else ACTION_CARD_CODEX_REVIEW
    if escalated:
        card_type = ACTION_CARD_CODEX_REVIEW
    title_prefix = "Review loop escalation" if escalated else "Refine loop behavior"
    details = evidence.get("details") if isinstance(evidence.get("details"), dict) else {}
    candidate_spec_id = str(details.get("candidate_spec_id") or _candidate_spec_for(phase=phase, action=action))
    recent_tool_chain = _string_list(details.get("recent_tool_chain"))
    last_tool_outputs = _string_list(details.get("last_tool_outputs"))
    next_iteration_guidance = str(details.get("next_iteration_guidance") or "").strip()
    executable_diagnosis = _executable_diagnosis(
        evidence=evidence,
        recent_tool_chain=recent_tool_chain,
        last_tool_outputs=last_tool_outputs,
        next_iteration_guidance=next_iteration_guidance,
    )
    proposal_contract = _proposal_contract_for_card(
        site_key=site_key,
        phase=phase,
        pattern=pattern,
        target_ref=target_ref,
        candidate_spec_id=candidate_spec_id,
        evidence_id=str(evidence.get("evidence_id") or ""),
    )
    suggested_actions = [
        "Read the loop-control evidence and recent tool chain before editing files.",
        "Produce a concrete proposal before repeating the same workflow: a run_local_overlay for batch-local validation, or another supported rollbackable proposal type for durable changes.",
        "Do not treat this card, the evidence, or a generic refinement hint as the proposal itself.",
        "Prefer Skill, workflow memory, or profile/application fact updates before Python runtime changes.",
        "Do not hard-code site-specific business behavior in Python unless the evidence is transport/runtime specific.",
        "After updating the target, rerun the same site/phase and verify the next item no longer repeats this pattern.",
    ]
    if action == LOOP_ACTION_REQUEST_USER_INPUT:
        suggested_actions = [
            "Ask the user for the missing factual answer.",
            "Store durable user facts in the appropriate profile/application context before rerunning.",
            "Do not guess private or compliance-sensitive facts.",
        ]
    related_files = _related_files_for_card(
        target_ref=target_ref,
        source_ref=str(evidence.get("source_ref") or ""),
    )
    metadata = {
        "site_key": site_key,
        "phase": phase,
        "batch_id": batch_id,
        "loop_control_action": action,
        "failure_pattern": pattern,
        "target_ref": target_ref,
        "evidence_id": evidence.get("evidence_id"),
        "candidate_id": candidate.get("candidate_id") if candidate else "",
        "candidate_spec_id": candidate_spec_id,
        "escalated": escalated,
        "recent_tool_chain": recent_tool_chain,
        "last_tool_outputs": last_tool_outputs,
        "executable_diagnosis": executable_diagnosis,
        "next_iteration_guidance": next_iteration_guidance,
        "required_output": "concrete_evolution_proposal",
        "accepted_proposal_types": sorted(SUPPORTED_CHANGE_TYPES),
        "proposal_contract": proposal_contract,
    }
    dedupe_key = f"loop_control:{site_key}:{phase}:{pattern}:{action}"
    store = ActionCardStore(workspace)
    existing = store.find_by_dedupe_key(dedupe_key)
    commands = [
        "python -m careereng assistant context",
        f"python -m careereng evolution candidate-show {candidate_spec_id}" if candidate_spec_id else "",
        f"python -m careereng evolution run -c {candidate_spec_id}" if candidate_spec_id else "",
        "python -m careereng action-card show <card_id>",
        "python -m careereng evolution apply --run <run_id>",
    ]
    commands = [item for item in commands if item]
    goal = summary + "\n\n" + executable_diagnosis
    reason = str(evidence.get("summary") or summary)
    if existing:
        existing_metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
        history = existing_metadata.get("loop_evidence_history") if isinstance(existing_metadata.get("loop_evidence_history"), list) else []
        evidence_id = str(evidence.get("evidence_id") or "")
        history.append(
            {
                "batch_id": batch_id,
                "evidence_id": evidence_id,
                "escalated": escalated,
                "current_item_ref": str(details.get("current_item_ref") or ""),
                "summary": str(evidence.get("summary") or summary),
            }
        )
        metadata["loop_evidence_history"] = history[-12:]
        return store.update_card_metadata(
            str(existing.get("card_id") or ""),
            metadata=metadata,
            related_files=related_files,
            commands=commands,
            suggested_actions=suggested_actions,
            goal=goal,
            reason=reason,
            priority="high" if escalated else "medium",
            summary=f"Updated loop-control action card with evidence {evidence_id}.",
        )
    return store.create_card(
        card_type=card_type,
        title=f"{title_prefix}: {site_key} {phase} {pattern}",
        goal=goal,
        reason=reason,
        source_type="loop_control",
        source_id=f"{site_key}:{phase}:{pattern}:{batch_id}",
        source_ref=str(evidence.get("source_ref") or ""),
        priority="high" if escalated else "medium",
        related_files=related_files,
        suggested_actions=suggested_actions,
        commands=commands,
        done_when=[
            "The target Skill/profile/config has been updated or a conscious no-change decision is recorded.",
            "A rerun validates that the next relevant loop item no longer repeats the same failure pattern.",
        ],
        metadata={**metadata, "loop_evidence_history": [metadata]},
        semantic_tags=["loop_engineering", site_key, phase, pattern, action, candidate_spec_id],
        dedupe_key=dedupe_key,
    )


def _merge_existing_candidate_evidence(
    *, store: OpenEvolutionCandidateStore, candidate: dict[str, Any], evidence_id: str
) -> dict[str, Any]:
    candidate_id = str(candidate.get("candidate_id") or "")
    if not candidate_id:
        return candidate
    evidence_ids = {str(evidence_id or "").strip()} if str(evidence_id or "").strip() else set()
    for row in store.read_all():
        if str(row.get("candidate_id") or "") != candidate_id:
            continue
        for item in row.get("evidence_ids") or []:
            text = str(item or "").strip()
            if text:
                evidence_ids.add(text)
        break
    merged = dict(candidate)
    merged["evidence_ids"] = sorted(evidence_ids)
    merged["evidence_count"] = len(evidence_ids)
    return merged


def _recommended_target_ref(*, recommended_target: str, project_root: Path, site_key: str) -> str:
    target = str(recommended_target or "").strip()
    normalized = target.lower().replace("-", "_").replace(" ", "_")
    if normalized in {"project_jobs_skill", "project_skill", "jobs_skill", "global_jobs_skill"}:
        return "skills/search/jobs/SKILL.md"
    if normalized in {"site_skill", "site_jobs_skill"}:
        return f"skills/search/jobs/sites/{safe_file_stem(site_key)}/SKILL.md"
    if normalized in {"application_profile", "profile", "user_profile", "user_fact"}:
        return "workspace/profile/application_profile.md"
    if target:
        try:
            path = Path(target)
            if path.is_absolute():
                return str(path.resolve().relative_to(project_root.resolve()))
        except Exception:
            pass
        return target
    return f"skills/search/jobs/sites/{safe_file_stem(site_key)}/SKILL.md"


def _previous_failed_batch_count(*, workspace: Path, site_key: str, phase: str, pattern: str, current_batch_id: str) -> int:
    store = EvolutionEvidenceStore(workspace)
    batch_ids: set[str] = set()
    for row in store.read_all():
        if str(row.get("area") or "") != "loop_engineering":
            continue
        if str(row.get("site_key") or "") != site_key or str(row.get("phase") or "") != phase:
            continue
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        if str(details.get("failure_pattern") or "") != pattern:
            continue
        batch_id = str(details.get("batch_id") or "")
        if batch_id and batch_id != current_batch_id:
            batch_ids.add(batch_id)
    return len(batch_ids)


def _summary_for_control(*, site_name: str, phase: str, pattern: str, action: str) -> str:
    return f"{site_name} {phase} loop requested `{action}` for pattern `{pattern}`."


def _default_suggested_change(action: str) -> str:
    if action == LOOP_ACTION_TRIGGER_REFINEMENT:
        return "Refine the relevant Skill or workflow guidance using the attached page evidence."
    if action == LOOP_ACTION_REQUEST_USER_INPUT:
        return "Collect the missing user fact and persist it in the appropriate local profile context."
    return "Review the loop-control evidence and decide whether to refine, retry, or pause."


def _candidate_spec_for(*, phase: str, action: str) -> str:
    if action == LOOP_ACTION_TRIGGER_REFINEMENT:
        return LOOP_CANDIDATE_SPEC_BY_PHASE.get(str(phase or "").strip(), "site_workflow_compaction")
    return ""


def _executable_diagnosis(
    *,
    evidence: dict[str, Any],
    recent_tool_chain: list[str],
    last_tool_outputs: list[str],
    next_iteration_guidance: str,
) -> str:
    details = evidence.get("details") if isinstance(evidence.get("details"), dict) else {}
    lines = [
        "Executable diagnosis for the next loop item:",
        f"- Current item: {details.get('title') or details.get('url') or details.get('job_id') or 'unknown'}",
        f"- Pattern: {details.get('failure_pattern') or 'unknown'}",
    ]
    if recent_tool_chain:
        lines.append(f"- Recent tool chain: {' -> '.join(recent_tool_chain[-8:])}")
    if last_tool_outputs:
        lines.append("- Last observed tool outputs:")
        lines.extend([f"  - {item}" for item in last_tool_outputs[-3:]])
    if next_iteration_guidance:
        lines.append(f"- Next iteration guidance: {next_iteration_guidance}")
    else:
        lines.append(
            "- Next iteration guidance: before repeating the same action chain, refresh the live page, inspect the visible blocker or next action, then either complete the item to a terminal state or write a structured loop-control gap."
        )
    return "\n".join(lines)


def _related_files_for_card(*, target_ref: str, source_ref: str) -> list[str]:
    files: list[str] = []
    for item in (
        target_ref,
        "skills/search/jobs/SKILL.md",
        source_ref,
        "docs/evolution/EVOLUTION_RUN_PROTOCOL.md",
        "docs/evolution/PROPOSAL_SCHEMA.md",
        "evolution/browser_control/lessons.jsonl",
    ):
        text = str(item or "").strip()
        if text and text not in files:
            files.append(text)
    return files


def _proposal_contract_for_card(
    *,
    site_key: str,
    phase: str,
    pattern: str,
    target_ref: str,
    candidate_spec_id: str,
    evidence_id: str,
) -> dict[str, Any]:
    return {
        "required_output": "concrete_evolution_proposal",
        "purpose": "Codex or another assistant must convert the evidence into an executable proposal before the same failed strategy is retried.",
        "allowed_change_types": sorted(SUPPORTED_CHANGE_TYPES),
        "preferred_first_change": "run_local_overlay",
        "evolution_run_command": f"python -m careereng evolution run -c {candidate_spec_id}" if candidate_spec_id else "",
        "proposal_output_path": "workspace/evolution/runs/<run_id>/proposals/proposal.json",
        "apply_command": "python -m careereng evolution apply --run <run_id>",
        "target_ref": target_ref,
        "source": {
            "site_key": site_key,
            "phase": phase,
            "failure_pattern": pattern,
            "evidence_id": evidence_id,
        },
        "minimum_fields": [
            "run_id",
            "candidate_id",
            "diagnosis",
            "proposed_changes",
            "proposed_changes[].change_type",
            "proposed_changes[].content or proposed_changes[].replacement_markdown",
            "validation_plan",
        ],
        "rejection_rules": [
            "Do not submit a summary-only response.",
            "Do not treat evidence, action-card text, or a generic refinement hint as a proposal.",
            "Do not propose Python code for site-specific form behavior.",
        ],
    }


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    return f"{prefix}_{_fingerprint(payload)[:16]}"


def _fingerprint(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()
