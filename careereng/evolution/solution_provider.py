"""Prepare Codex-readable solution requests for evolution runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from careereng.evolution.work_items import ActionCardError, ActionCardStore
from careereng.evolution.candidate_specs import CandidateSpecError
from careereng.evolution.evidence_pack import build_solution_evidence_pack
from careereng.evolution.proposals import FORBIDDEN_CHANGE_TYPES, SUPPORTED_CHANGE_TYPES, proposal_path_for_run
from careereng.evolution.runs import create_evolution_run
from careereng.utils import now_iso, read_json, write_json


class EvolutionSolutionError(ValueError):
    """Raised when a solution request cannot be prepared."""


def create_solution_request_for_action_card(
    *,
    project_root: Path | str,
    workspace: Path | str,
    card_id: str,
    candidate_id: str = "",
    context_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an evolution run and solution request for one action card.

    This is intentionally a handoff layer. It packages evidence and a concrete
    proposal contract for Codex; it does not infer the site/workflow solution.
    """

    root = Path(project_root)
    workspace_path = Path(workspace)
    store = ActionCardStore(workspace_path)
    try:
        card = store.show_card(card_id)
    except ActionCardError as exc:
        raise EvolutionSolutionError(str(exc)) from exc
    metadata = card.get("metadata") if isinstance(card.get("metadata"), dict) else {}
    spec_id = _candidate_spec_id(metadata=metadata, fallback=candidate_id)
    if not spec_id:
        raise EvolutionSolutionError("candidate_id is required when action card metadata has no candidate_spec_id.")
    context = _context_from_card(card, metadata=metadata)
    try:
        run = create_evolution_run(
            project_root=root,
            workspace=workspace_path,
            candidate_id=spec_id,
            context=context,
        )
    except CandidateSpecError as exc:
        raise EvolutionSolutionError(str(exc)) from exc

    request = create_solution_request_for_run(
        project_root=root,
        workspace=workspace_path,
        run_id=str(run["run_id"]),
        card_id=str(card.get("card_id") or card_id),
        context_overrides=context_overrides or {},
    )
    updated_card = store.update_card_metadata(
        str(card.get("card_id") or card_id),
        metadata={
            "solution_run_id": request["run_id"],
            "solution_request": str(request["solution_request"]),
            "evidence_pack": str(request["evidence_pack"]),
            "evidence_pack_json": str(request["evidence_pack_json"]),
            "proposal_output_path": str(request["proposal_output_path"]),
            "evolution_run_dir": str(request["run_dir"]),
        },
        related_files=[
            str(_workspace_relative(workspace_path, request["solution_request"])),
            str(_workspace_relative(workspace_path, request["proposal_output_path"])),
            str(_workspace_relative(workspace_path, request["evidence_pack"])),
            str(_workspace_relative(workspace_path, request["evidence_pack_json"])),
        ],
        commands=[
            f"python -m careereng evolution solution --card {card.get('card_id') or card_id}",
            f"python -m careereng evolution apply --run {request['run_id']}",
        ],
        done_when=[
            "A valid proposal.json has been written to the proposal output path.",
            "The proposal has been applied with `python -m careereng evolution apply --run <run_id>`.",
        ],
        summary=f"Linked solution request {request['solution_request']} to action card.",
    )
    return {**request, "action_card_id": str(updated_card.get("card_id") or card_id)}


def create_solution_request_for_run(
    *,
    project_root: Path | str,
    workspace: Path | str,
    run_id: str,
    card_id: str = "",
    context_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(project_root)
    workspace_path = Path(workspace)
    normalized_run = str(run_id or "").strip()
    if not normalized_run:
        raise EvolutionSolutionError("run_id is required.")
    run_dir = workspace_path / "evolution" / "runs" / normalized_run
    run_json = run_dir / "run.json"
    run_payload = read_json(run_json)
    if not run_payload:
        raise EvolutionSolutionError(f"Unknown evolution run: {normalized_run}")
    if context_overrides:
        context = run_payload.setdefault("context", {})
        if isinstance(context, dict):
            context.update({key: value for key, value in context_overrides.items() if value not in (None, "", [], {})})
            write_json(run_json, run_payload)

    card: dict[str, Any] = {}
    card_markdown = ""
    normalized_card = str(card_id or _context_value(run_payload, "action_card_id") or "").strip()
    if normalized_card:
        store = ActionCardStore(workspace_path)
        try:
            card = store.show_card(normalized_card)
            card_markdown = store.markdown_text(normalized_card)
        except ActionCardError:
            card = {}
            card_markdown = ""

    proposal_path = proposal_path_for_run(run_dir)
    solution_request = run_dir / "solution_request.md"
    detailed_pack = build_solution_evidence_pack(
        project_root=root,
        workspace=workspace_path,
        run_dir=run_dir,
        run_payload=run_payload,
        card=card,
        card_markdown=card_markdown,
    )
    evidence_pack = Path(detailed_pack["markdown_path"])
    evidence_pack_json = Path(detailed_pack["json_path"])
    request_text = _render_solution_request(
        root=root,
        workspace=workspace_path,
        run_dir=run_dir,
        run_payload=run_payload,
        card=card,
        card_markdown=card_markdown,
        evidence_pack=evidence_pack,
        evidence_pack_json=evidence_pack_json,
        solution_request=solution_request,
        proposal_path=proposal_path,
    )
    solution_request.write_text(request_text, encoding="utf-8")

    now = now_iso()
    run_payload["status"] = "waiting_solution"
    run_payload["updated_at"] = now
    outputs = run_payload.setdefault("outputs", {})
    outputs["solution_request"] = str(solution_request)
    outputs["proposal"] = str(Path("proposals") / "proposal.json")
    outputs["evidence_pack"] = str(evidence_pack)
    outputs["evidence_pack_json"] = str(evidence_pack_json)
    lifecycle = run_payload.setdefault("lifecycle", [])
    if isinstance(lifecycle, list):
        lifecycle.append(
            {
                "status": "waiting_solution",
                "at": now,
                "summary": "Solution request created for Codex or another assistant to write a concrete proposal.",
            }
        )
    write_json(run_json, run_payload)
    _write_solution_summary(
        run_dir=run_dir,
        run_payload=run_payload,
        solution_request=solution_request,
        proposal_path=proposal_path,
    )
    return {
        "run_id": normalized_run,
        "status": "waiting_solution",
        "candidate_id": str(run_payload.get("candidate_id") or ""),
        "run_dir": run_dir,
        "run_json": run_json,
        "evidence_pack": evidence_pack,
        "evidence_pack_json": evidence_pack_json,
        "summary": run_dir / "summary.md",
        "solution_request": solution_request,
        "proposal_output_path": proposal_path,
        "action_card_id": normalized_card,
    }


def _candidate_spec_id(*, metadata: dict[str, Any], fallback: str) -> str:
    for key in ("candidate_spec_id", "evolution_candidate_spec_id", "candidate_spec"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return str(fallback or "").strip()


def _context_from_card(card: dict[str, Any], *, metadata: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "action_card_id": card.get("card_id"),
        "action_card_type": card.get("card_type"),
        "action_card_status": card.get("status"),
        "action_card_title": card.get("title"),
        "source_type": card.get("source_type"),
        "source_id": card.get("source_id"),
        "source_ref": card.get("source_ref"),
        "site_key": metadata.get("site_key"),
        "phase": metadata.get("phase"),
        "batch_id": metadata.get("batch_id"),
        "failure_pattern": metadata.get("failure_pattern"),
        "loop_control_action": metadata.get("loop_control_action"),
        "target_ref": metadata.get("target_ref"),
        "evidence_id": metadata.get("evidence_id"),
        "candidate_id": metadata.get("candidate_id"),
        "candidate_spec_id": metadata.get("candidate_spec_id"),
        "proposal_contract": metadata.get("proposal_contract"),
    }
    return {key: value for key, value in fields.items() if value not in (None, "", [], {})}


def _render_solution_request(
    *,
    root: Path,
    workspace: Path,
    run_dir: Path,
    run_payload: dict[str, Any],
    card: dict[str, Any],
    card_markdown: str,
    evidence_pack: Path,
    evidence_pack_json: Path,
    solution_request: Path,
    proposal_path: Path,
) -> str:
    candidate = run_payload.get("candidate") if isinstance(run_payload.get("candidate"), dict) else {}
    context = run_payload.get("context") if isinstance(run_payload.get("context"), dict) else {}
    card_metadata = card.get("metadata") if isinstance(card.get("metadata"), dict) else {}
    proposal_contract = _solution_level_contract(
        card_metadata.get("proposal_contract") if isinstance(card_metadata.get("proposal_contract"), dict) else {},
        context=context,
    )
    outer_synthesis = _is_outer_synthesis_context(context)
    skeleton = _proposal_skeleton(run_payload=run_payload, candidate=candidate, context=context, outer_synthesis=outer_synthesis)
    lines = [
        "# Codex Evolution Solution Request",
        "",
        "This file is a work order for Codex or another local AI assistant.",
        "",
        "## Required Output",
        "",
        f"- Write a valid JSON proposal to: `{proposal_path}`",
        "- Do not answer in prose only.",
        "- Do not treat this request, the action card, evidence, or a generic hint as the proposal.",
        "- Synthesize the full proposal/validation chain from the evidence pack into a new proposal.",
        "- Start from the evolution strategy router and candidate spec; use the evidence index to choose which local evidence to inspect.",
        "- Do not copy an old run-local overlay unchanged unless the evidence supports another validation pass.",
        "- The proposal must validate against `docs/evolution/PROPOSAL_SCHEMA.md`.",
        (
            "- This is an outer-loop synthesis request: synthesize a concrete batch-level proposal from the evidence. "
            "Use supported proposal changes; do not add Python business logic."
            if outer_synthesis
            else "- This is an inner-loop or short-horizon request: `run_local_overlay` is allowed when it changes the next unit's behavior."
        ),
        "",
        "## Boundary",
        "",
        "- Python packages evidence, validates proposal shape, applies rollbackable changes, and records results.",
        "- Codex/LLM writes the concrete strategy or Skill/memory/context change.",
        "- Codex/LLM should synthesize across proposal history and validation results; Python must not choose the business strategy.",
        "- Python may provide starter excerpts and indexed paths, but Codex/LLM must decide which evidence matters.",
        "- Do not hard-code site-specific form behavior, job matching policy, or website workflow decisions in Python.",
        "",
        "## Supported Change Types",
        "",
        *[f"- `{item}`" for item in sorted(SUPPORTED_CHANGE_TYPES)],
        "",
        (
            "Outer-loop synthesis should normally use durable supported changes such as `skill_patch`, "
            "`memory_unit_append`, `routing_example_append`, or `assistant_context_update` when the evidence supports them. "
            "If the evidence supports only a short-horizon experiment, use `run_local_overlay` and explain the validation plan."
            if outer_synthesis
            else "Preferred first change for in-batch workflow refinement: `run_local_overlay`."
        ),
        "",
        "## Forbidden Change Types",
        "",
        *[f"- `{item}`" for item in sorted(FORBIDDEN_CHANGE_TYPES)],
        "",
        "## Run",
        "",
        f"- Run ID: `{run_payload.get('run_id')}`",
        f"- Candidate: `{run_payload.get('candidate_id')}`",
        f"- Candidate Target: `{candidate.get('target_ref') or ''}`",
        f"- Solution Request: `{solution_request}`",
        f"- Evidence Pack: `{evidence_pack}`",
        f"- Evidence Pack JSON: `{evidence_pack_json}`",
        f"- Run Directory: `{run_dir}`",
        "",
        "## Context",
        "",
        *_format_mapping(context),
        "",
        "## Proposal Contract From Action Card",
        "",
        _format_json_block(proposal_contract or {"note": "No action-card proposal contract found."}),
        "",
        "## Action Card",
        "",
        card_markdown.strip() if card_markdown.strip() else "_No action card markdown was available._",
        "",
        "## Evidence Pack Excerpt",
        "",
        _read_excerpt(evidence_pack),
        "",
        "## Proposal Skeleton",
        "",
        "Use this skeleton as a starting point and replace placeholder text with a concrete solution:",
        "",
        _format_json_block(skeleton),
        "",
        "## After Writing Proposal",
        "",
        f"1. Run: `python -m careereng evolution apply --run {run_payload.get('run_id')}`",
        "2. Rerun the target workflow unit and verify the proposal is injected as active run-local or durable context.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _is_outer_synthesis_context(context: dict[str, Any]) -> bool:
    return (
        str(context.get("solution_level") or "").strip() == "outer_synthesis"
        or str(context.get("solution_request_kind") or "").strip() == "synthesis_work_order"
    )


def _solution_level_contract(contract: dict[str, Any], *, context: dict[str, Any]) -> dict[str, Any]:
    payload = dict(contract or {})
    if not _is_outer_synthesis_context(context):
        return payload
    payload["preferred_first_change"] = payload.get("preferred_first_change") or "skill_patch"
    payload["batch_level_synthesis"] = True
    rejection_rules = payload.get("rejection_rules") if isinstance(payload.get("rejection_rules"), list) else []
    payload["rejection_rules"] = [
        *[str(item) for item in rejection_rules],
        "Do not answer with prose only.",
        "Do not add Python business logic for site workflow, matching policy, or form-filling strategy.",
        "Use the evidence pack and candidate spec to choose supported proposal changes.",
    ]
    return payload


def _proposal_skeleton(
    *,
    run_payload: dict[str, Any],
    candidate: dict[str, Any],
    context: dict[str, Any],
    outer_synthesis: bool,
) -> dict[str, Any]:
    if outer_synthesis:
        return {
            "run_id": run_payload.get("run_id"),
            "candidate_id": run_payload.get("candidate_id"),
            "provider": "codex",
            "diagnosis": "Replace this with an evidence-backed batch-level synthesis.",
            "proposed_changes": [
                {
                    "change_id": "change_1",
                    "change_type": "site_mode_update",
                    "site_key": str(context.get("site_key") or ""),
                    "mode": "ready",
                    "summary": "Replace with Codex's evidence-backed readiness decision.",
                },
                {
                    "change_id": "change_2",
                    "change_type": "skill_patch",
                    "summary": "Optional durable site-skill refinement supported by batch-level evidence.",
                    "target_file": str(context.get("target_ref") or candidate.get("target_ref") or ""),
                    "target_section": "Apply",
                    "patch_strategy": "replace_section",
                    "replacement_markdown": "Replace this with the complete updated Markdown section when the evidence supports a Skill change.",
                    "source_evidence_id": context.get("evidence_id") or "",
                    "confidence": 0.7,
                }
            ],
            "evaluation_plan": [
                "Run the next outer-loop batch and verify the repeated failure pattern is reduced or replaced by a more specific terminal outcome.",
                "Record whether the durable change led to submitted, filtered_out, blocked_new_reason, or repeated_same_failure.",
            ],
            "risk_notes": [],
        }
    return {
        "run_id": run_payload.get("run_id"),
        "candidate_id": run_payload.get("candidate_id"),
        "provider": "codex",
        "diagnosis": "Replace this with an evidence-backed diagnosis.",
        "proposed_changes": [
            {
                "change_id": "change_1",
                "change_type": "run_local_overlay",
                "summary": "Concrete strategy to validate on the next unit.",
                "scope": f"batch:{context.get('batch_id') or '<batch_id>'}:site:{context.get('site_key') or '<site_key>'}:{context.get('phase') or '<phase>'}",
                "site_key": context.get("site_key") or "",
                "phase": context.get("phase") or "",
                "pattern": context.get("failure_pattern") or "",
                "content": "Write the exact prompt overlay the next job/run must follow. This must be a real strategy change, not a summary of evidence.",
                "source_evidence_id": context.get("evidence_id") or "",
                "action_card": context.get("action_card_id") or "",
                "target_ref": context.get("target_ref") or candidate.get("target_ref") or "",
                "expected_validation": "State what the next job/run must prove.",
                "confidence": 0.65,
            }
        ],
        "evaluation_plan": [
            "The next unit should not repeat the same failure pattern unchanged.",
            "Record whether the proposed change led to submitted, filtered_out, blocked_new_reason, or repeated_same_failure.",
        ],
        "risk_notes": [],
    }


def _write_solution_summary(
    *,
    run_dir: Path,
    run_payload: dict[str, Any],
    solution_request: Path,
    proposal_path: Path,
) -> None:
    candidate = run_payload.get("candidate") if isinstance(run_payload.get("candidate"), dict) else {}
    context = run_payload.get("context") if isinstance(run_payload.get("context"), dict) else {}
    lines = [
        "# Evolution Run Summary",
        "",
        f"- Run ID: `{run_payload.get('run_id')}`",
        f"- Status: `{run_payload.get('status')}`",
        f"- Candidate: `{run_payload.get('candidate_id')}`",
        f"- Target: `{candidate.get('target_ref') or ''}`",
        f"- Site: `{context.get('site_key') or ''}`",
        f"- Phase: `{context.get('phase') or ''}`",
        f"- Action Card: `{context.get('action_card_id') or ''}`",
        "",
        "## Current Stage",
        "",
        "A Codex-readable solution request has been created. No proposal has been applied yet.",
        "",
        "## Required Output",
        "",
        f"- Solution request: `{solution_request}`",
        f"- Proposal output: `{proposal_path}`",
        "",
        "## Next Expected Stage",
        "",
        "Codex or another assistant writes `proposal.json`, then `python -m careereng evolution apply --run <run_id>` applies it.",
    ]
    (run_dir / "summary.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _format_mapping(data: dict[str, Any]) -> list[str]:
    if not data:
        return ["- No context provided."]
    lines: list[str] = []
    for key in sorted(data):
        value = data.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (dict, list)):
            value_text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            value_text = str(value)
        lines.append(f"- `{key}`: `{value_text}`")
    return lines or ["- No displayable context."]


def _format_json_block(data: dict[str, Any]) -> str:
    return "```json\n" + json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n```"


def _read_excerpt(path: Path, *, max_chars: int = 8000) -> str:
    if not path.exists():
        return f"_Missing evidence pack: `{path}`._"
    text = path.read_text(encoding="utf-8").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n\n_Excerpt truncated. Read the full evidence pack before writing the proposal._"


def _context_value(run_payload: dict[str, Any], key: str) -> str:
    context = run_payload.get("context") if isinstance(run_payload.get("context"), dict) else {}
    return str(context.get(key) or "").strip()


def _workspace_relative(workspace: Path, path: Path | str) -> Path:
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(workspace.resolve())
    except Exception:
        return candidate
