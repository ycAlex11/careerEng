"""Detailed evidence packs for assistant-written evolution proposals.

This module packages local evidence for Codex/LLM solution work. It does not
infer site behavior, matching policy, or form-filling strategy.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from careereng.action_cards.store import ActionCardStore
from careereng.evolution.browser_control.lessons import BrowserControlLessonStore
from careereng.evolution.memory_units import RUN_LOCAL_CLOSED_FOR_SYNTHESIS, EvolutionMemoryStore
from careereng.evolution.strategy_router import related_strategy_spec_payloads, strategy_family, strategy_router_payload
from careereng.storage.jsonl import JSONLStore
from careereng.utils import now_iso, read_json, safe_file_stem, write_json


MAX_TEXT_CHARS = 12_000
MAX_SECTION_CHARS = 6_000
MAX_TRACE_OUTPUT_CHARS = 1_500
MAX_TRACE_EVENTS = 12
MAX_FAILURE_EXAMPLES = 12


def build_solution_evidence_pack(
    *,
    project_root: Path | str,
    workspace: Path | str,
    run_dir: Path | str,
    run_payload: dict[str, Any],
    card: dict[str, Any] | None = None,
    card_markdown: str = "",
) -> dict[str, Any]:
    """Write a detailed evidence pack for one pending solution run."""

    root = Path(project_root)
    workspace_path = Path(workspace)
    run_path = Path(run_dir)
    context = run_payload.get("context") if isinstance(run_payload.get("context"), dict) else {}
    candidate = run_payload.get("candidate") if isinstance(run_payload.get("candidate"), dict) else {}
    normalized_card = card if isinstance(card, dict) else {}

    site_key = str(context.get("site_key") or "").strip()
    phase = str(context.get("phase") or "").strip()
    batch_id = str(context.get("batch_id") or "").strip()
    target_ref = str(context.get("target_ref") or candidate.get("target_ref") or "").strip()
    failure_pattern = str(context.get("failure_pattern") or "").strip()
    candidate_id = str(run_payload.get("candidate_id") or candidate.get("id") or "").strip()

    workflow_summary = _workflow_summary(workspace_path=workspace_path, batch_id=batch_id)
    batch_state = _batch_state(workspace_path=workspace_path, batch_id=batch_id)
    run_rows = _run_rows(workspace_path=workspace_path, site_key=site_key, batch_id=batch_id)
    failure_rows = _failure_rows(run_rows, failure_pattern=failure_pattern)
    selected_rows = failure_rows[:MAX_FAILURE_EXAMPLES]
    failure_snapshot = _failure_snapshot(workspace_path=workspace_path, site_key=site_key, batch_id=batch_id, phase=phase)
    trace_refs = _trace_refs(
        workspace_path=workspace_path,
        site_key=site_key,
        rows=selected_rows,
        site_row=(batch_state.get("sites") or {}).get(site_key) if isinstance(batch_state.get("sites"), dict) else {},
        failure_snapshot=failure_snapshot,
    )

    payload = {
        "pack_id": f"solution_evidence_pack:{run_payload.get('run_id') or ''}",
        "generated_at": now_iso(),
        "run": _run_brief(run_payload),
        "context": context,
        "candidate": _candidate_brief(candidate),
        "strategy": {
            "family": strategy_family(candidate_id),
            "router": strategy_router_payload(root),
            "related_specs": related_strategy_spec_payloads(root, candidate_id=candidate_id, max_chars=MAX_TEXT_CHARS),
            "selection_contract": (
                "Python provides an evidence index and starter excerpts. Codex/LLM must choose which indexed "
                "evidence to inspect according to the router and candidate specs."
            ),
        },
        "action_card": _action_card_brief(normalized_card),
        "workflow_summary": workflow_summary,
        "batch_state": _batch_brief(batch_state, site_key=site_key),
        "run_rows": {
            "path": str(_run_rows_path(workspace_path=workspace_path, site_key=site_key, batch_id=batch_id)),
            "total": len(run_rows),
            "failure_examples": [_row_brief(row) for row in selected_rows],
        },
        "failure_snapshot": failure_snapshot,
        "trace_excerpts": [_trace_excerpt(workspace_path=workspace_path, ref=ref) for ref in trace_refs],
        "skill_sections": _skill_sections(
            project_root=root,
            target_ref=target_ref,
            site_key=site_key,
            phase=phase,
        ),
        "proposal_history": _proposal_history(
            workspace_path=workspace_path,
            batch_id=batch_id,
            site_key=site_key,
            phase=phase,
            failure_pattern=failure_pattern,
        ),
        "related_evolution_context": _related_evolution_context(
            workspace_path=workspace_path,
            site_key=site_key,
            phase=phase,
        ),
        "action_card_markdown_excerpt": _truncate(card_markdown.strip(), MAX_TEXT_CHARS),
        "proposal_contract": _proposal_contract(normalized_card, context),
        "source_paths": _source_paths(
            project_root=root,
            workspace_path=workspace_path,
            run_dir=run_path,
            batch_id=batch_id,
            site_key=site_key,
            phase=phase,
            target_ref=target_ref,
            trace_refs=trace_refs,
        ),
    }
    payload["evidence_index"] = _evidence_index(
        payload.get("source_paths") if isinstance(payload.get("source_paths"), dict) else {},
        strategy=payload.get("strategy") if isinstance(payload.get("strategy"), dict) else {},
        run_rows_total=len(run_rows),
        failure_examples=len(selected_rows),
        trace_refs=trace_refs,
    )

    json_path = run_path / "evidence_pack.json"
    markdown_path = run_path / "evidence_pack.md"
    write_json(json_path, payload)
    markdown_path.write_text(render_solution_evidence_pack_markdown(payload), encoding="utf-8")
    return {
        "json_path": json_path,
        "markdown_path": markdown_path,
        "payload": payload,
    }


def render_solution_evidence_pack_markdown(payload: dict[str, Any]) -> str:
    run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    lines = [
        "# Detailed Evolution Solution Evidence Pack",
        "",
        "This pack is evidence for Codex/LLM proposal writing. It is not a proposal.",
        "",
        "## Run",
        "",
        f"- Run ID: `{run.get('run_id') or ''}`",
        f"- Candidate: `{run.get('candidate_id') or ''}`",
        f"- Status: `{run.get('status') or ''}`",
        f"- Site: `{context.get('site_key') or ''}`",
        f"- Phase: `{context.get('phase') or ''}`",
        f"- Batch: `{context.get('batch_id') or ''}`",
        f"- Failure Pattern: `{context.get('failure_pattern') or ''}`",
        "",
        "## Proposal Boundary",
        "",
        "- Python packaged this evidence only.",
        "- Codex/LLM must write the concrete proposal.",
        (
            "- This is an outer-loop synthesis pack. Use the candidate spec and evidence index to produce a concrete proposal with supported changes."
            if _is_outer_synthesis_context(context)
            else "- Prefer `skill_patch` for durable site-skill changes and `run_local_overlay` for the next in-batch validation."
        ),
        "- Do not propose Python code for site-specific workflow behavior.",
        "- Use the strategy router and evidence index to decide what to inspect; starter excerpts below are not exhaustive.",
        "",
        "## Strategy Router",
        "",
        _strategy_markdown(payload.get("strategy")),
        "",
        "## Evidence Index",
        "",
        _json_block(payload.get("evidence_index")),
        "",
        "## Workflow Summary",
        "",
        _json_block(payload.get("workflow_summary")),
        "",
        "## Batch State",
        "",
        _json_block(payload.get("batch_state")),
        "",
        "## Failure Examples",
        "",
    ]
    run_rows = payload.get("run_rows") if isinstance(payload.get("run_rows"), dict) else {}
    examples = run_rows.get("failure_examples") if isinstance(run_rows.get("failure_examples"), list) else []
    if not examples:
        lines.append("- No failure examples were found for the requested pattern.")
    else:
        for example in examples:
            lines.append(
                f"- job=`{example.get('job_id') or ''}` title={example.get('title') or ''} "
                f"status={example.get('application_status') or ''}/{example.get('decision_status') or ''} "
                f"pattern=`{example.get('failure_pattern') or ''}`"
            )
            evidence = str(example.get("evidence") or example.get("last_apply_error") or "").strip()
            if evidence:
                lines.append(f"  Evidence: {evidence[:800]}")
            current_ref = str(example.get("current_item_ref") or example.get("url") or "").strip()
            if current_ref:
                lines.append(f"  Ref: {current_ref[:500]}")
    lines.extend(["", "## Failure Snapshot Excerpt", ""])
    snapshot = payload.get("failure_snapshot") if isinstance(payload.get("failure_snapshot"), dict) else {}
    if snapshot.get("excerpt"):
        lines.extend([f"- Path: `{snapshot.get('path')}`", "", "```markdown", str(snapshot.get("excerpt") or ""), "```"])
    else:
        lines.append("- No failure snapshot excerpt found.")
    lines.extend(["", "## Starter Trace Excerpts", ""])
    traces = payload.get("trace_excerpts") if isinstance(payload.get("trace_excerpts"), list) else []
    if not traces:
        lines.append("- No trace excerpts found.")
    else:
        for trace in traces:
            lines.append(f"### `{trace.get('ref') or trace.get('path') or ''}`")
            lines.append("")
            lines.append(_json_block(trace.get("events")))
            lines.append("")
    lines.extend(["", "## Relevant Skill Sections", ""])
    skill_sections = payload.get("skill_sections") if isinstance(payload.get("skill_sections"), list) else []
    if not skill_sections:
        lines.append("- No skill sections found.")
    else:
        for section in skill_sections:
            lines.append(f"### `{section.get('path')}` / {section.get('heading') or 'excerpt'}")
            lines.append("")
            lines.append("```markdown")
            lines.append(str(section.get("text") or ""))
            lines.append("```")
            lines.append("")
    lines.extend(["## Proposal Usage And Validation History", "", _json_block(payload.get("proposal_history")), ""])
    lines.extend(["## Related Evolution Context", "", _json_block(payload.get("related_evolution_context")), ""])
    contract = payload.get("proposal_contract") if isinstance(payload.get("proposal_contract"), dict) else {}
    lines.extend(["## Required Output Contract", "", _json_block(contract), ""])
    paths = payload.get("source_paths") if isinstance(payload.get("source_paths"), dict) else {}
    lines.extend(["## Source Paths", "", _json_block(paths)])
    return "\n".join(lines).rstrip() + "\n"


def _strategy_markdown(value: Any) -> str:
    strategy = value if isinstance(value, dict) else {}
    router = strategy.get("router") if isinstance(strategy.get("router"), dict) else {}
    related_specs = strategy.get("related_specs") if isinstance(strategy.get("related_specs"), list) else []
    lines = [
        f"- Strategy Family: `{strategy.get('family') or ''}`",
        f"- Router Path: `{router.get('relative_path') or router.get('path') or ''}`",
        f"- Router Status: `{router.get('status') or ''}`",
        f"- Selection Contract: {strategy.get('selection_contract') or ''}",
        "",
        "### Related Strategy Specs",
    ]
    if not related_specs:
        lines.append("- No related strategy specs found.")
    else:
        for spec in related_specs:
            if not isinstance(spec, dict):
                continue
            lines.append(
                f"- `{spec.get('id') or ''}` path=`{spec.get('relative_path') or spec.get('path') or ''}` status=`{spec.get('status') or ''}`"
            )
    router_text = str(router.get("text") or "").strip()
    if router_text:
        lines.extend(["", "### Router Text", "", "```markdown", router_text, "```"])
    return "\n".join(lines).rstrip()


def _workflow_summary(*, workspace_path: Path, batch_id: str) -> dict[str, Any]:
    if not batch_id:
        return {}
    json_path = workspace_path / "evolution" / "workflow_summaries" / f"{safe_file_stem(batch_id)}.json"
    payload = read_json(json_path)
    if not payload:
        return {"path": str(json_path), "status": "missing"}
    return {
        "path": str(json_path),
        "batch_status": str(payload.get("batch_status") or ""),
        "sites_with_loop_evidence": int(payload.get("sites_with_loop_evidence") or 0),
        "lesson_candidates_written": int(payload.get("lesson_candidates_written") or 0),
        "next_actions": payload.get("next_actions") if isinstance(payload.get("next_actions"), list) else [],
        "sites": payload.get("sites") if isinstance(payload.get("sites"), list) else [],
    }


def _batch_state(*, workspace_path: Path, batch_id: str) -> dict[str, Any]:
    if not batch_id:
        return {}
    return read_json(workspace_path / "jobs" / "batches" / f"{safe_file_stem(batch_id)}.json")


def _batch_brief(batch: dict[str, Any], *, site_key: str) -> dict[str, Any]:
    if not batch:
        return {}
    sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
    site = sites.get(site_key) if isinstance(sites.get(site_key), dict) else {}
    return {
        "batch_id": str(batch.get("batch_id") or ""),
        "status": str(batch.get("status") or ""),
        "operation": str(batch.get("operation") or ""),
        "apply_requested": bool(batch.get("apply_requested")),
        "site": site,
    }


def _run_rows_path(*, workspace_path: Path, site_key: str, batch_id: str) -> Path:
    return workspace_path / "sites" / safe_file_stem(site_key) / "jobs" / "runs" / f"{safe_file_stem(batch_id)}.jsonl"


def _run_rows(*, workspace_path: Path, site_key: str, batch_id: str) -> list[dict[str, Any]]:
    path = _run_rows_path(workspace_path=workspace_path, site_key=site_key, batch_id=batch_id)
    return JSONLStore(path).read_all() if path.exists() else []


def _failure_rows(rows: list[dict[str, Any]], *, failure_pattern: str) -> list[dict[str, Any]]:
    target = str(failure_pattern or "").strip()
    failures: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pattern = str(row.get("failure_pattern") or row.get("block_reason_type") or "").strip()
        action = str(row.get("loop_control_action") or row.get("recommended_action") or "").strip()
        status = str(row.get("application_status") or row.get("decision_status") or "").strip().lower()
        if target and pattern == target:
            failures.append(row)
        elif not target and (pattern or action not in {"", "continue"} or status in {"blocked", "apply_failed", "failed"}):
            failures.append(row)
    if failures or not target:
        return failures
    for row in rows:
        if not isinstance(row, dict):
            continue
        pattern = str(row.get("failure_pattern") or row.get("block_reason_type") or "").strip()
        action = str(row.get("loop_control_action") or row.get("recommended_action") or "").strip()
        status = str(row.get("application_status") or row.get("decision_status") or "").strip().lower()
        if pattern or action not in {"", "continue"} or status in {"blocked", "apply_failed", "failed"}:
            failures.append(row)
    return failures


def _row_brief(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "job_id",
        "canonical_job_id",
        "site_job_id",
        "title",
        "url",
        "current_item_ref",
        "application_status",
        "decision_status",
        "apply_state",
        "failure_pattern",
        "loop_control_action",
        "block_reason_type",
        "last_apply_error",
        "evidence",
        "active_run_local_proposal_id",
        "active_run_local_proposal_memory_id",
        "trace_ref",
    )
    return {key: row.get(key) for key in keys if row.get(key) not in (None, "", [], {})}


def _failure_snapshot(*, workspace_path: Path, site_key: str, batch_id: str, phase: str) -> dict[str, str]:
    if not site_key or not batch_id or not phase:
        return {}
    path = (
        workspace_path
        / "sites"
        / safe_file_stem(site_key)
        / "evolution"
        / "failure_snapshots"
        / f"{safe_file_stem(batch_id)}_{safe_file_stem(phase)}.md"
    )
    if not path.exists():
        return {"path": str(path), "status": "missing"}
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return {
        "path": str(path),
        "status": "found",
        "trace_refs": _extract_trace_refs(text),
        "excerpt": _truncate(text, MAX_TEXT_CHARS),
    }


def _trace_refs(
    *,
    workspace_path: Path,
    site_key: str,
    rows: list[dict[str, Any]],
    site_row: dict[str, Any],
    failure_snapshot: dict[str, Any],
) -> list[str]:
    refs: list[str] = []
    for row in rows:
        value = str(row.get("trace_ref") or "").strip()
        if value:
            refs.append(value)
    site_trace = str(site_row.get("trace_ref") or "").strip() if isinstance(site_row, dict) else ""
    if site_trace:
        refs.append(site_trace)
    for value in failure_snapshot.get("trace_refs") or []:
        if str(value).strip():
            refs.append(str(value).strip())
    if site_key:
        traces_dir = workspace_path / "sites" / safe_file_stem(site_key) / "events" / "traces"
        if traces_dir.exists():
            latest = sorted([path for path in traces_dir.glob("*.jsonl") if path.is_file()], key=lambda path: path.stat().st_mtime)
            refs.extend(str(path) for path in latest[-3:])
    deduped: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        key = str(ref).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped[-5:]


def _extract_trace_refs(text: str) -> list[str]:
    refs = re.findall(r"Trace:\s+`([^`]+)`", text)
    refs.extend(re.findall(r"(?:workspace/)?sites/[^`\s]+/events/traces/[^`\s]+\.jsonl", text))
    return refs


def _trace_excerpt(*, workspace_path: Path, ref: str) -> dict[str, Any]:
    path = _resolve_workspace_path(workspace_path, ref)
    if not path.exists():
        return {"ref": ref, "path": str(path), "status": "missing", "events": []}
    rows = JSONLStore(path).read_all()
    events = []
    for row in rows[-MAX_TRACE_EVENTS:]:
        if not isinstance(row, dict):
            continue
        events.append(
            {
                "ts": str(row.get("ts") or ""),
                "phase": str(row.get("phase") or ""),
                "step_id": str(row.get("step_id") or ""),
                "tool_name": str(row.get("tool_name") or ""),
                "result": str(row.get("result") or ""),
                "arguments": _compact_json_value(row.get("arguments")),
                "output_excerpt": _truncate(str(row.get("output") or ""), MAX_TRACE_OUTPUT_CHARS),
            }
        )
    return {"ref": ref, "path": str(path), "status": "found", "event_count": len(rows), "events": events}


def _resolve_workspace_path(workspace_path: Path, ref: str) -> Path:
    path = Path(str(ref or ""))
    if path.is_absolute():
        return path
    if str(path).startswith("workspace/"):
        return workspace_path.parent / path
    return workspace_path / path


def _skill_sections(*, project_root: Path, target_ref: str, site_key: str, phase: str) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    target = _resolve_project_path(project_root, target_ref)
    headings = _section_names_for_phase(phase)
    if target.exists():
        sections.extend(_extract_markdown_sections(target, headings=headings, fallback_chars=MAX_SECTION_CHARS))
    project_skill = project_root / "skills" / "search" / "jobs" / "SKILL.md"
    if project_skill.exists():
        sections.extend(_extract_markdown_sections(project_skill, headings=headings, fallback_chars=MAX_SECTION_CHARS))
    if site_key and not target.exists():
        site_skill = project_root / "skills" / "search" / "jobs" / "sites" / safe_file_stem(site_key) / "SKILL.md"
        if site_skill.exists():
            sections.extend(_extract_markdown_sections(site_skill, headings=headings, fallback_chars=MAX_SECTION_CHARS))
    return sections[:12]


def _resolve_project_path(project_root: Path, target_ref: str) -> Path:
    ref = str(target_ref or "").strip()
    if not ref:
        return project_root / "__missing_target_ref__"
    path = Path(ref)
    return path if path.is_absolute() else project_root / path


def _section_names_for_phase(phase: str) -> tuple[str, ...]:
    normalized = safe_file_stem(str(phase or "")).replace("-", "_")
    common = ("Site Policy", "Matching Policy")
    if normalized == "apply":
        return (*common, "Apply", "Form Filling", "Application Status Review")
    if normalized == "job_retrieval":
        return (*common, "Job Retrieval")
    if normalized == "job_filtering":
        return (*common, "Job Filtering")
    if normalized == "channel_discovery":
        return (*common, "Channel Discovery")
    return common


def _extract_markdown_sections(path: Path, *, headings: tuple[str, ...], fallback_chars: int) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = _markdown_heading_blocks(text)
    selected: list[dict[str, str]] = []
    targets = {heading.lower(): heading for heading in headings}
    for heading, body in blocks:
        if heading.lower() not in targets:
            continue
        selected.append({"path": str(path), "heading": heading, "text": _truncate(body.strip(), fallback_chars)})
    if selected:
        return selected
    return [{"path": str(path), "heading": "file excerpt", "text": _truncate(text.strip(), fallback_chars)}]


def _markdown_heading_blocks(text: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"^(#{1,6})\s+(.+?)\s*$", text, flags=re.MULTILINE))
    blocks: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        heading = match.group(2).strip()
        blocks.append((heading, text[start:end].strip()))
    return blocks


def _proposal_history(
    *,
    workspace_path: Path,
    batch_id: str,
    site_key: str,
    phase: str,
    failure_pattern: str,
) -> list[dict[str, Any]]:
    if not batch_id or not site_key:
        return []
    rows = EvolutionMemoryStore(workspace_path).query(
        scopes=[f"batch:{batch_id}:site:{site_key}:{phase}"],
        phase=phase,
        lifecycles=["run_local", "candidate", "accepted"],
        statuses=["active", RUN_LOCAL_CLOSED_FOR_SYNTHESIS, "candidate", "accepted", "rejected", "expired", "superseded"],
        limit=80,
    )
    history: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if failure_pattern and str(row.get("pattern") or "") not in {"", failure_pattern}:
            continue
        proposal = row.get("proposal") if isinstance(row.get("proposal"), dict) else {}
        history.append(
            {
                "memory_id": str(row.get("memory_id") or ""),
                "candidate_id": str(row.get("candidate_id") or ""),
                "scope": str(row.get("scope") or ""),
                "status": str(row.get("status") or ""),
                "pattern": str(row.get("pattern") or ""),
                "summary": str(row.get("summary") or "")[:800],
                "proposal": {
                    "proposal_id": str(proposal.get("proposal_id") or ""),
                    "proposal_kind": str(proposal.get("proposal_kind") or ""),
                    "proposal_status": str(proposal.get("proposal_status") or ""),
                    "materialized_change_type": str((proposal.get("materialized_change") or {}).get("type") or "")
                    if isinstance(proposal.get("materialized_change"), dict)
                    else "",
                    "expected_validation": str(proposal.get("expected_validation") or "")[:800],
                    "prompt_overlay": str(proposal.get("prompt_overlay") or "")[:1200],
                },
                "usage_events": _compact_events(row.get("usage_events")),
                "validation_events": _compact_events(row.get("validation_events")),
                "close_events": _compact_close_events(row.get("close_events")),
            }
        )
    return history[-30:]


def _related_evolution_context(*, workspace_path: Path, site_key: str, phase: str) -> dict[str, Any]:
    lessons = BrowserControlLessonStore(workspace_path).accepted(phase=phase, limit=12)
    memories = EvolutionMemoryStore(workspace_path).query(
        lifecycles=["candidate", "accepted"],
        statuses=["candidate", "accepted"],
        limit=12,
    )
    cards = ActionCardStore(workspace_path).list_cards(status="open", limit=12)
    return {
        "accepted_browser_control_lessons": [_related_lesson_brief(row) for row in lessons],
        "evolution_memory_units": [_related_memory_brief(row) for row in memories],
        "open_action_cards": [_related_action_card_brief(row) for row in cards],
        "selection_policy": (
            "These are indexed candidates for Codex/LLM inspection. Python does not decide whether any lesson "
            f"applies to {site_key}:{phase}."
        ),
    }


def _related_lesson_brief(row: dict[str, Any]) -> dict[str, Any]:
    origin = row.get("evidence_origin") if isinstance(row.get("evidence_origin"), dict) else {}
    return {
        "lesson_id": str(row.get("lesson_id") or ""),
        "origin_site_key": str(origin.get("site_key") or row.get("site_key") or ""),
        "phase": str(row.get("phase") or ""),
        "lesson_type": str(row.get("lesson_type") or ""),
        "applicability_scope": str(row.get("applicability_scope") or row.get("scope") or ""),
        "summary": str(row.get("summary") or "")[:500],
        "recommended_patterns": _string_list(row.get("recommended_patterns"))[:5],
        "avoid_patterns": _string_list(row.get("avoid_patterns"))[:5],
        "tags": _string_list(row.get("applicability_tags") or row.get("applies_to"))[:8],
    }


def _related_memory_brief(row: dict[str, Any]) -> dict[str, Any]:
    proposal = row.get("proposal") if isinstance(row.get("proposal"), dict) else {}
    materialized_change = proposal.get("materialized_change") if isinstance(proposal.get("materialized_change"), dict) else {}
    return {
        "memory_id": str(row.get("memory_id") or ""),
        "candidate_id": str(row.get("candidate_id") or ""),
        "scope": str(row.get("scope") or ""),
        "site_key": str(row.get("site_key") or ""),
        "phase": str(row.get("phase") or ""),
        "lifecycle": str(row.get("lifecycle") or ""),
        "status": str(row.get("status") or ""),
        "pattern": str(row.get("pattern") or ""),
        "summary": str(row.get("summary") or "")[:500],
        "proposal_id": str(proposal.get("proposal_id") or ""),
        "materialized_change_type": str(materialized_change.get("type") or ""),
    }


def _related_action_card_brief(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "card_id": str(card.get("card_id") or ""),
        "card_type": str(card.get("card_type") or ""),
        "status": str(card.get("status") or ""),
        "priority": str(card.get("priority") or ""),
        "title": str(card.get("title") or ""),
        "source_type": str(card.get("source_type") or ""),
        "source_id": str(card.get("source_id") or ""),
    }


def _compact_close_events(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    compact: list[dict[str, Any]] = []
    for event in value[-10:]:
        if not isinstance(event, dict):
            continue
        compact.append(
            {
                key: event.get(key)
                for key in ("closed_at", "status", "reason", "run_id")
                if event.get(key) not in (None, "", [], {})
            }
        )
    return compact


def _compact_events(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    compact: list[dict[str, Any]] = []
    for event in value[-10:]:
        if not isinstance(event, dict):
            continue
        compact.append(
            {
                key: event.get(key)
                for key in (
                    "recorded_at",
                    "batch_id",
                    "site_key",
                    "phase",
                    "job_id",
                    "title",
                    "proposal_id",
                    "pattern",
                    "result",
                    "application_status",
                    "decision_status",
                    "failure_pattern",
                    "loop_control_action",
                )
                if event.get(key) not in (None, "", [], {})
            }
        )
    return compact


def _proposal_contract(card: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    metadata = card.get("metadata") if isinstance(card.get("metadata"), dict) else {}
    contract = metadata.get("proposal_contract") if isinstance(metadata.get("proposal_contract"), dict) else {}
    payload = {
        "required_output": contract.get("required_output") or "concrete_evolution_proposal",
        "allowed_change_types": contract.get("allowed_change_types")
        or ["run_local_overlay", "skill_patch", "memory_unit_append", "routing_example_append", "assistant_context_update"],
        "preferred_first_change": contract.get("preferred_first_change") or "run_local_overlay",
        "target_ref": contract.get("target_ref") or context.get("target_ref") or "",
        "minimum_fields": contract.get("minimum_fields")
        or ["run_id", "candidate_id", "diagnosis", "proposed_changes", "validation_plan"],
        "rejection_rules": contract.get("rejection_rules")
        or [
            "Do not submit a summary-only response.",
            "Do not treat evidence, action-card text, or a generic refinement hint as a proposal.",
            "Do not propose Python code for site-specific workflow behavior.",
        ],
    }
    if _is_outer_synthesis_context(context):
        payload["preferred_first_change"] = contract.get("preferred_first_change") or "skill_patch"
        payload["batch_level_synthesis"] = True
        payload["rejection_rules"] = [
            *payload["rejection_rules"],
            "For outer-loop synthesis, do not answer with prose only.",
            "Use supported proposal changes instead of Python business logic.",
            "Explain how the next batch should validate the proposal in evaluation_plan.",
        ]
    return payload


def _is_outer_synthesis_context(context: dict[str, Any]) -> bool:
    return (
        str(context.get("solution_level") or "").strip() == "outer_synthesis"
        or str(context.get("solution_request_kind") or "").strip() == "synthesis_work_order"
    )


def _source_paths(
    *,
    project_root: Path,
    workspace_path: Path,
    run_dir: Path,
    batch_id: str,
    site_key: str,
    phase: str,
    target_ref: str,
    trace_refs: list[str],
) -> dict[str, Any]:
    return {
        "project_root": str(project_root),
        "workspace": str(workspace_path),
        "run_dir": str(run_dir),
        "strategy_router": str(project_root / "docs" / "evolution" / "EVOLUTION_STRATEGY_ROUTER.md"),
        "candidate_specs_dir": str(project_root / "docs" / "evolution" / "candidates"),
        "workflow_summary_json": str(workspace_path / "evolution" / "workflow_summaries" / f"{safe_file_stem(batch_id)}.json"),
        "workflow_summary_md": str(workspace_path / "evolution" / "workflow_summaries" / f"{safe_file_stem(batch_id)}.md"),
        "batch_json": str(workspace_path / "jobs" / "batches" / f"{safe_file_stem(batch_id)}.json"),
        "run_rows_jsonl": str(_run_rows_path(workspace_path=workspace_path, site_key=site_key, batch_id=batch_id)),
        "failure_snapshot": str(
            workspace_path
            / "sites"
            / safe_file_stem(site_key)
            / "evolution"
            / "failure_snapshots"
            / f"{safe_file_stem(batch_id)}_{safe_file_stem(phase)}.md"
        ),
        "target_skill": str(_resolve_project_path(project_root, target_ref)),
        "project_jobs_skill": str(project_root / "skills" / "search" / "jobs" / "SKILL.md"),
        "accepted_browser_control_lessons": str(workspace_path / "evolution" / "browser_control" / "lessons.jsonl"),
        "evolution_memory_units": str(workspace_path / "evolution" / "memory" / "units.jsonl"),
        "action_cards_index": str(workspace_path / "action_cards" / "index.jsonl"),
        "trace_refs": trace_refs,
    }


def _evidence_index(
    source_paths: dict[str, Any],
    *,
    strategy: dict[str, Any],
    run_rows_total: int,
    failure_examples: int,
    trace_refs: list[str],
) -> dict[str, Any]:
    related_specs = strategy.get("related_specs") if isinstance(strategy.get("related_specs"), list) else []
    router = strategy.get("router") if isinstance(strategy.get("router"), dict) else {}
    return {
        "principle": "Codex/LLM chooses which indexed evidence to inspect; Python does not choose business evidence.",
        "strategy_family": strategy.get("family") or "",
        "strategy_router": router.get("path") or "",
        "related_strategy_specs": [
            {"id": spec.get("id"), "path": spec.get("path"), "status": spec.get("status")}
            for spec in related_specs
            if isinstance(spec, dict)
        ],
        "counts": {
            "run_rows_total": int(run_rows_total or 0),
            "failure_examples_in_starter_excerpt": int(failure_examples or 0),
            "trace_refs_indexed": len(trace_refs),
        },
        "data_sources": [
            {"name": name, "path": value}
            for name, value in source_paths.items()
            if name != "trace_refs" and value not in (None, "", [], {})
        ],
        "trace_refs": list(trace_refs),
        "related_evolution_context": {
            "accepted_browser_control_lessons": source_paths.get("accepted_browser_control_lessons") or "",
            "evolution_memory_units": source_paths.get("evolution_memory_units") or "",
            "action_cards_index": source_paths.get("action_cards_index") or "",
            "selection_policy": "Codex/LLM inspects these sources and decides whether any lesson is relevant; Python only indexes them.",
        },
        "starter_excerpt_warning": "Trace/skill/failure excerpts are convenience context only; inspect indexed sources when writing a durable proposal.",
    }


def _run_brief(run_payload: dict[str, Any]) -> dict[str, str]:
    return {
        "run_id": str(run_payload.get("run_id") or ""),
        "candidate_id": str(run_payload.get("candidate_id") or ""),
        "status": str(run_payload.get("status") or ""),
        "created_at": str(run_payload.get("created_at") or ""),
        "updated_at": str(run_payload.get("updated_at") or ""),
    }


def _candidate_brief(candidate: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(candidate.get("id") or ""),
        "name": str(candidate.get("name") or ""),
        "target_type": str(candidate.get("target_type") or ""),
        "target_ref": str(candidate.get("target_ref") or ""),
        "risk_level": str(candidate.get("risk_level") or ""),
        "apply_policy": str(candidate.get("apply_policy") or ""),
        "body_excerpt": _truncate(str(candidate.get("body") or ""), MAX_TEXT_CHARS),
    }


def _action_card_brief(card: dict[str, Any]) -> dict[str, Any]:
    if not card:
        return {}
    return {
        "card_id": str(card.get("card_id") or ""),
        "card_type": str(card.get("card_type") or ""),
        "status": str(card.get("status") or ""),
        "priority": str(card.get("priority") or ""),
        "title": str(card.get("title") or ""),
        "goal": str(card.get("goal") or ""),
        "reason": str(card.get("reason") or ""),
        "metadata": card.get("metadata") if isinstance(card.get("metadata"), dict) else {},
    }


def _compact_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _compact_json_value(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_compact_json_value(item) for item in value[:20]]
    if isinstance(value, str):
        return _truncate(value, 500)
    return value


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value if value is not None else {}, ensure_ascii=False, indent=2, sort_keys=True) + "\n```"


def _truncate(text: str, max_chars: int) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + "\n...[truncated]"
