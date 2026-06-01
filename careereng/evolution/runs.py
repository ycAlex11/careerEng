"""Create archived evolution runs from candidate specs and local evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from careereng.evolution.candidate_specs import CandidateSpec, get_candidate_spec
from careereng.storage.jsonl import JSONLStore
from careereng.utils import ensure_dir, make_id, now_iso, read_json, write_json


RECENT_EVIDENCE_LIMIT = 40
OPEN_CANDIDATE_LIMIT = 30


def create_evolution_run(
    *,
    project_root: Path | str,
    workspace: Path | str,
    candidate_id: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(project_root)
    workspace_path = Path(workspace)
    spec = get_candidate_spec(root, candidate_id)
    created_at = now_iso()
    run_id = make_id("evo_run")
    run_dir = ensure_dir(workspace_path / "evolution" / "runs" / run_id)
    for child in ("snapshots", "proposals", "evaluations", "retention"):
        ensure_dir(run_dir / child)

    inputs = _run_inputs(workspace_path)
    context_payload = _normalize_context(context)
    evidence_pack_path = run_dir / "evidence_pack.md"
    summary_path = run_dir / "summary.md"
    run_json_path = run_dir / "run.json"
    candidate_payload = _candidate_payload(spec, root=root)
    run_payload: dict[str, Any] = {
        "run_id": run_id,
        "created_at": created_at,
        "updated_at": created_at,
        "status": "created",
        "candidate_id": spec.id,
        "candidate": candidate_payload,
        "context": context_payload,
        "inputs": {key: str(value) for key, value in inputs.items()},
        "outputs": {
            "run_json": str(run_json_path),
            "evidence_pack": str(evidence_pack_path),
            "summary": str(summary_path),
            "proposal": "",
            "applied_patch": "",
            "evaluation": "",
            "retention": "",
        },
        "lifecycle": [
            {"status": "created", "at": created_at, "summary": "Evolution run archive created."},
        ],
        "apply_policy": {
            "mode": spec.apply_policy,
            "auto_apply_allowed": False,
            "requires_snapshot": True,
            "requires_rollback_path": True,
        },
        "rollback": {
            "available": False,
            "snapshot_dir": str(run_dir / "snapshots"),
            "restored_at": "",
            "reason": "",
        },
        "evaluation": {
            "status": "not_started",
            "evaluation_dir": str(run_dir / "evaluations"),
            "selection_status": "not_started",
        },
        "selection": {
            "status": "not_started",
            "decision_at": "",
            "reason": "",
        },
    }

    evidence_pack_path.write_text(
        _render_evidence_pack(spec=spec, run_payload=run_payload, workspace=workspace_path, context=context_payload),
        encoding="utf-8",
    )
    summary_path.write_text(_render_summary(run_payload), encoding="utf-8")
    write_json(run_json_path, run_payload)
    return {
        "run_id": run_id,
        "status": "created",
        "run_dir": run_dir,
        "run_json": run_json_path,
        "evidence_pack": evidence_pack_path,
        "summary": summary_path,
        "candidate_id": spec.id,
    }


def _run_inputs(workspace: Path) -> dict[str, Path]:
    return {
        "context_pack": workspace / "evolution" / "context" / "latest.md",
        "open_candidates": workspace / "evolution" / "candidates" / "open.jsonl",
        "evidence": workspace / "evolution" / "evidence" / "all.jsonl",
        "memory_units": workspace / "evolution" / "memory" / "units.jsonl",
        "application_summary": workspace / "application_summary" / "application_summary.json",
        "metrics_usage": workspace / "metrics" / "llm_usage.jsonl",
        "browser_phase_events": workspace / "evolution" / "browser_control" / "phase_events.jsonl",
        "assistant_intake_events": workspace / "assistant_bridge" / "intake_events.jsonl",
        "assistant_routing_examples": workspace / "assistant_bridge" / "routing_examples.jsonl",
        "assistant_corrections": workspace / "assistant_bridge" / "correction_events.jsonl",
        "career_memory_units": workspace / "memory" / "memory_units.jsonl",
    }


def _candidate_payload(spec: CandidateSpec, *, root: Path) -> dict[str, Any]:
    payload = spec.to_dict()
    path = Path(str(payload.get("path") or ""))
    try:
        payload["path"] = str(path.relative_to(root))
    except ValueError:
        payload["path"] = str(path)
    return payload


def _normalize_context(context: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(context, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key, value in context.items():
        name = str(key or "").strip()
        if not name:
            continue
        if isinstance(value, Path):
            normalized[name] = str(value)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            normalized[name] = value
        elif isinstance(value, list):
            normalized[name] = [str(item) for item in value if str(item).strip()]
        elif isinstance(value, dict):
            normalized[name] = {str(k): str(v) for k, v in value.items() if str(k).strip()}
        else:
            normalized[name] = str(value)
    return normalized


def _render_evidence_pack(
    *,
    spec: CandidateSpec,
    run_payload: dict[str, Any],
    workspace: Path,
    context: dict[str, Any],
) -> str:
    inputs = _run_inputs(workspace)
    lines = [
        "# Evolution Run Evidence Pack",
        "",
        "## Run",
        "",
        f"- Run ID: `{run_payload['run_id']}`",
        f"- Status: `{run_payload['status']}`",
        f"- Created: {run_payload['created_at']}",
        "",
        "## Candidate",
        "",
        f"- ID: `{spec.id}`",
        f"- Name: {spec.name}",
        f"- Target Type: `{spec.target_type}`",
        f"- Target Ref: `{spec.target_ref}`",
        f"- Risk Level: `{spec.risk_level}`",
        f"- Apply Policy: `{spec.apply_policy}`",
        f"- Spec Path: `{_relative_or_str(spec.path, workspace.parent)}`",
        "",
        "## Target Site Context",
        "",
        *_format_context(context),
        "",
        "## Candidate Spec Body",
        "",
        spec.body or "_No candidate body found._",
        "",
        "## Current Evolution Context",
        "",
        _read_text_or_note(inputs["context_pack"], "No latest evolution context found. Run `python -m careereng evolution review` first for richer context."),
        "",
        "## Open Improvement Candidates",
        "",
        *_format_jsonl_rows(inputs["open_candidates"], limit=OPEN_CANDIDATE_LIMIT, fields=("candidate_id", "area", "target_ref", "priority", "summary")),
        "",
        "## Recent Evidence",
        "",
        *_format_jsonl_rows(inputs["evidence"], limit=RECENT_EVIDENCE_LIMIT, fields=("evidence_id", "area", "site_key", "phase", "event_type", "severity", "summary")),
        "",
        "## Application Summary Snapshot",
        "",
        *_format_json_object(inputs["application_summary"], keys=("generated_at", "totals", "stage_distribution", "status_distribution")),
        "",
        "## Relevant Local Paths",
        "",
    ]
    for name, path in inputs.items():
        lines.append(f"- `{name}`: `{path}`")
    lines.extend(
        [
            "",
            "## Next Expected Stage",
            "",
            "- Generate an LLM diagnosis/proposal from this evidence pack.",
            "- If a proposal is applied later, create snapshots first and record rollback paths.",
            "- Evaluate before retaining or rolling back the proposal.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _render_summary(run_payload: dict[str, Any]) -> str:
    candidate = run_payload.get("candidate") if isinstance(run_payload.get("candidate"), dict) else {}
    context = run_payload.get("context") if isinstance(run_payload.get("context"), dict) else {}
    target_lines = ""
    if context:
        target_lines = (
            f"- Site: `{context.get('site_key') or ''}` {context.get('site_name') or ''}\n"
            f"- Target Skill: `{context.get('target_skill') or ''}`\n"
            f"- Action Card: `{context.get('action_card_id') or ''}`\n\n"
        )
    return (
        "# Evolution Run Summary\n\n"
        f"- Run ID: `{run_payload.get('run_id')}`\n"
        f"- Status: `{run_payload.get('status')}`\n"
        f"- Candidate: `{run_payload.get('candidate_id')}`\n"
        f"- Target: `{candidate.get('target_ref') or ''}`\n"
        f"- Risk: `{candidate.get('risk_level') or ''}`\n"
        f"- Apply Policy: `{candidate.get('apply_policy') or ''}`\n\n"
        f"{target_lines}"
        "## Current Stage\n\n"
        "The run archive and evidence pack have been created. No LLM proposal has been generated, no files have been applied, and no evaluation has started.\n\n"
        "## Next Expected Stage\n\n"
        "Generate a proposal, snapshot rollbackable targets before applying, then evaluate and select accepted/rejected/keep_observing.\n"
    )


def _read_text_or_note(path: Path, note: str) -> str:
    if not path.exists():
        return f"_{note}_"
    text = path.read_text(encoding="utf-8").strip()
    return text or f"_{note}_"


def _format_jsonl_rows(path: Path, *, limit: int, fields: tuple[str, ...]) -> list[str]:
    if not path.exists():
        return [f"- No data found at `{path}`."]
    rows = JSONLStore(path).read_all()
    if not rows:
        return [f"- No rows found at `{path}`."]
    lines: list[str] = []
    for row in rows[-limit:]:
        if not isinstance(row, dict):
            continue
        parts = []
        for field in fields:
            value = row.get(field)
            if value in (None, "", [], {}):
                continue
            parts.append(f"{field}={_inline_value(value)}")
        lines.append(f"- {'; '.join(parts) if parts else json.dumps(row, ensure_ascii=False, sort_keys=True)}")
    return lines or [f"- No displayable rows found at `{path}`."]


def _format_json_object(path: Path, *, keys: tuple[str, ...]) -> list[str]:
    if not path.exists():
        return [f"- No data found at `{path}`."]
    data = read_json(path)
    if not data:
        return [f"- No JSON object found at `{path}`."]
    lines = []
    for key in keys:
        if key in data:
            lines.append(f"- `{key}`: `{_inline_value(data.get(key))}`")
    return lines or [f"- No selected keys found at `{path}`."]


def _format_context(context: dict[str, Any]) -> list[str]:
    if not context:
        return ["- No target context was provided."]
    lines: list[str] = []
    for key in sorted(context):
        value = context.get(key)
        if value in (None, "", [], {}):
            continue
        lines.append(f"- `{key}`: `{_inline_value(value)}`")
    return lines or ["- Target context was empty."]


def _inline_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    text = " ".join(text.split())
    if len(text) > 260:
        return text[:257].rstrip() + "..."
    return text


def _relative_or_str(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
