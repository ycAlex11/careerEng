"""Batch-level workflow evolution summaries.

This module is the bridge from short-term loop evidence to long-term evolution
memory. It summarizes what happened in a batch and may write candidate lessons,
but it never accepts lessons or applies skill changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from careereng.evolution.browser_control.lessons import BrowserControlLessonStore
from careereng.evolution.memory_units import EvolutionMemoryStore
from careereng.storage.jsonl import JSONLStore
from careereng.utils import ensure_dir, now_iso, safe_file_stem, write_json


def generate_workflow_evolution_summary(
    *,
    workspace: Path | str,
    batch: dict[str, Any],
) -> dict[str, Any]:
    workspace_path = Path(workspace)
    batch_id = str(batch.get("batch_id") or "").strip()
    if not batch_id:
        return {}
    sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
    site_summaries: list[dict[str, Any]] = []
    lesson_candidates = 0
    for site_key, site_row in sites.items():
        if not isinstance(site_row, dict):
            continue
        summary = _site_loop_summary(
            workspace=workspace_path,
            batch_id=batch_id,
            site_key=str(site_key),
            site_row=site_row,
        )
        if summary.get("loop_pattern_count") or summary.get("guidance_count"):
            site_summaries.append(summary)
            lesson_candidates += _write_candidate_lessons(workspace=workspace_path, batch_id=batch_id, site_summary=summary)

    payload = {
        "batch_id": batch_id,
        "generated_at": now_iso(),
        "batch_status": str(batch.get("status") or ""),
        "operation": str(batch.get("operation") or ""),
        "apply_requested": bool(batch.get("apply_requested")),
        "site_count": len(sites),
        "sites_with_loop_evidence": len(site_summaries),
        "lesson_candidates_written": lesson_candidates,
        "evolution_decisions": _compact_evolution_decisions(batch),
        "sites": site_summaries,
        "next_actions": _next_actions(site_summaries),
    }
    out_dir = ensure_dir(workspace_path / "evolution" / "workflow_summaries")
    json_path = out_dir / f"{safe_file_stem(batch_id)}.json"
    markdown_path = out_dir / f"{safe_file_stem(batch_id)}.md"
    write_json(json_path, payload)
    markdown_path.write_text(_render_markdown(payload), encoding="utf-8")
    payload["json_path"] = str(json_path)
    payload["markdown_path"] = str(markdown_path)
    return payload


def _site_loop_summary(*, workspace: Path, batch_id: str, site_key: str, site_row: dict[str, Any]) -> dict[str, Any]:
    run_rows = _read_run_rows(workspace=workspace, site_key=site_key, batch_id=batch_id)
    context = _read_json(workspace / "sites" / safe_file_stem(site_key) / "jobs" / "runs" / f"{safe_file_stem(batch_id)}.context.json")
    guidance = context.get("apply_loop_refinement_guidance") if isinstance(context.get("apply_loop_refinement_guidance"), list) else []
    memory_units = EvolutionMemoryStore(workspace).query(
        candidate_id="site_apply_loop_control",
        scopes=[f"batch:{batch_id}:site:{site_key}:apply", f"site:{site_key}:apply"],
        phase="apply",
        lifecycles=["run_local"],
        statuses=["active"],
        limit=20,
    )
    grouped: dict[str, dict[str, Any]] = {}
    for row in run_rows:
        action = str(row.get("loop_control_action") or row.get("recommended_action") or "").strip()
        if not action or action == "continue":
            continue
        phase = "apply"
        pattern = safe_file_stem(str(row.get("failure_pattern") or row.get("block_reason_type") or "unknown")).replace("-", "_")
        key = f"{phase}:{pattern}:{action}"
        item = grouped.setdefault(
            key,
            {
                "phase": phase,
                "pattern": pattern,
                "action": action,
                "count": 0,
                "titles": [],
                "evidence": [],
                "latest_target": str(row.get("recommended_target") or row.get("target") or ""),
            },
        )
        item["count"] = int(item.get("count") or 0) + 1
        title = str(row.get("title") or "").strip()
        if title and title not in item["titles"]:
            item["titles"].append(title)
        evidence = str(row.get("evidence") or row.get("last_apply_error") or "").strip()
        if evidence and evidence not in item["evidence"]:
            item["evidence"].append(evidence[:600])
    patterns = sorted(grouped.values(), key=lambda row: int(row.get("count") or 0), reverse=True)
    return {
        "site_key": site_key,
        "site_name": str(site_row.get("site_name") or site_key),
        "site_status": str(site_row.get("status") or ""),
        "current_phase": str(site_row.get("current_phase") or ""),
        "reason_tag": str(site_row.get("reason_tag") or ""),
        "loop_pattern_count": len(patterns),
        "guidance_count": len(guidance),
        "patterns": patterns,
        "run_local_memory": _compact_memory_units(memory_units),
        "run_context": {
            "path": str(workspace / "sites" / safe_file_stem(site_key) / "jobs" / "runs" / f"{safe_file_stem(batch_id)}.context.json"),
            "apply_loop_refinement_summary": str(context.get("apply_loop_refinement_summary") or ""),
        },
    }


def _write_candidate_lessons(*, workspace: Path, batch_id: str, site_summary: dict[str, Any]) -> int:
    store = BrowserControlLessonStore(workspace)
    written = 0
    site_key = str(site_summary.get("site_key") or "")
    for pattern in site_summary.get("patterns") or []:
        if not isinstance(pattern, dict):
            continue
        phase = str(pattern.get("phase") or "")
        name = str(pattern.get("pattern") or "")
        action = str(pattern.get("action") or "")
        count = int(pattern.get("count") or 0)
        if not action or action == "continue":
            continue
        lesson, created = store.append_unique(
            {
                "lesson_id": f"lesson_candidate_{safe_file_stem(site_key)}_{safe_file_stem(phase)}_{safe_file_stem(name)}_{safe_file_stem(batch_id)}",
                "status": "candidate",
                "phase": phase,
                "lesson_type": "batch_workflow_loop_summary",
                "summary": f"{site_key} {phase} observed {count} `{action}` loop-control event(s) for `{name}` in batch {batch_id}.",
                "rationale": _lesson_rationale(site_summary=site_summary, pattern_name=name),
                "evidence_origin": {
                    "site_key": site_key,
                    "batch_id": batch_id,
                    "phase": phase,
                },
                "applicability_scope": "site_skill_evolution",
                "applicability_tags": [site_key, phase, name, "loop_engineering"],
                "evidence_refs": _lesson_evidence_refs(
                    batch_id=batch_id,
                    site_summary=site_summary,
                    pattern_name=name,
                ),
                "avoid_patterns": _lesson_avoid_patterns(site_summary=site_summary, pattern_name=name),
                "recommended_patterns": _lesson_recommended_patterns(site_summary=site_summary, pattern_name=name),
                "dedupe_key": f"lesson_candidate:{site_key}:{phase}:{name}:{batch_id}",
            }
        )
        if created and lesson:
            written += 1
    return written


def _compact_memory_units(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        compact.append(
            {
                "memory_id": str(unit.get("memory_id") or ""),
                "scope": str(unit.get("scope") or ""),
                "phase": str(unit.get("phase") or ""),
                "pattern": str(unit.get("pattern") or ""),
                "summary": str(unit.get("summary") or "")[:500],
                "avoid_patterns": [str(item) for item in (unit.get("avoid_patterns") or []) if str(item).strip()][:4],
                "recommended_patterns": [
                    str(item) for item in (unit.get("recommended_patterns") or []) if str(item).strip()
                ][:4],
                "source": unit.get("source") if isinstance(unit.get("source"), dict) else {},
                "proposal": _compact_proposal(unit.get("proposal") if isinstance(unit.get("proposal"), dict) else {}),
                "usage_events": _compact_usage_events(
                    unit.get("usage_events") if isinstance(unit.get("usage_events"), list) else []
                ),
                "validation_events": _compact_validation_events(
                    unit.get("validation_events") if isinstance(unit.get("validation_events"), list) else []
                ),
            }
        )
    return compact


def _compact_proposal(proposal: dict[str, Any]) -> dict[str, str]:
    if not proposal:
        return {}
    return {
        "proposal_id": str(proposal.get("proposal_id") or ""),
        "proposal_kind": str(proposal.get("proposal_kind") or ""),
        "proposal_status": str(proposal.get("proposal_status") or ""),
        "target_ref": str(proposal.get("target_ref") or ""),
        "expected_validation": str(proposal.get("expected_validation") or "")[:500],
        "materialized_change_type": str((proposal.get("materialized_change") or {}).get("type") or "")
        if isinstance(proposal.get("materialized_change"), dict)
        else "",
    }


def _compact_usage_events(events: list[Any]) -> list[dict[str, str]]:
    compact: list[dict[str, str]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        compact.append(
            {
                "job_id": str(event.get("job_id") or ""),
                "proposal_id": str(event.get("proposal_id") or ""),
                "pattern": str(event.get("pattern") or ""),
                "recorded_at": str(event.get("recorded_at") or ""),
            }
        )
    return compact[-10:]


def _compact_validation_events(events: list[Any]) -> list[dict[str, str]]:
    compact: list[dict[str, str]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        compact.append(
            {
                "job_id": str(event.get("job_id") or ""),
                "title": str(event.get("title") or ""),
                "result": str(event.get("result") or ""),
                "application_status": str(event.get("application_status") or ""),
                "failure_pattern": str(event.get("failure_pattern") or ""),
                "recorded_at": str(event.get("recorded_at") or ""),
            }
        )
    return compact[-10:]


def _compact_evolution_decisions(batch: dict[str, Any]) -> list[dict[str, str]]:
    loop = batch.get("evolution_loop") if isinstance(batch.get("evolution_loop"), dict) else {}
    decisions = loop.get("decisions") if isinstance(loop.get("decisions"), list) else []
    compact: list[dict[str, str]] = []
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        compact.append(
            {
                "decision_id": str(decision.get("decision_id") or ""),
                "verdict": str(decision.get("verdict") or ""),
                "site_key": str(decision.get("site_key") or ""),
                "phase": str(decision.get("phase") or ""),
                "failure_pattern": str(decision.get("failure_pattern") or ""),
                "target_ref": str(decision.get("target_ref") or ""),
                "validation_plan": str(decision.get("validation_plan") or "")[:500],
            }
        )
    return compact


def _memory_for_pattern(*, site_summary: dict[str, Any], pattern_name: str) -> list[dict[str, Any]]:
    rows = site_summary.get("run_local_memory") if isinstance(site_summary.get("run_local_memory"), list) else []
    target = str(pattern_name or "").strip()
    return [row for row in rows if isinstance(row, dict) and str(row.get("pattern") or "") == target]


def _lesson_avoid_patterns(*, site_summary: dict[str, Any], pattern_name: str) -> list[str]:
    patterns = [
        f"Repeating `{pattern_name}` without first applying batch-local guidance or updating the relevant Skill/workflow memory."
    ]
    for unit in _memory_for_pattern(site_summary=site_summary, pattern_name=pattern_name):
        for item in unit.get("avoid_patterns") or []:
            text = str(item or "").strip()
            if text and text not in patterns:
                patterns.append(text)
    return patterns[:8]


def _lesson_rationale(*, site_summary: dict[str, Any], pattern_name: str) -> str:
    lines = ["Candidate lesson only. Use this with later batch evidence before accepting or patching a skill."]
    proposal_notes = _proposal_validation_notes(site_summary=site_summary, pattern_name=pattern_name)
    if proposal_notes:
        lines.append("Run-local proposal evidence observed in this batch:")
        lines.extend(proposal_notes)
    return " ".join(lines)


def _lesson_evidence_refs(*, batch_id: str, site_summary: dict[str, Any], pattern_name: str) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = [
        {
            "type": "workflow_summary",
            "path": f"workspace/evolution/workflow_summaries/{safe_file_stem(batch_id)}.json",
        }
    ]
    for unit in _memory_for_pattern(site_summary=site_summary, pattern_name=pattern_name):
        memory_id = str(unit.get("memory_id") or "").strip()
        proposal = unit.get("proposal") if isinstance(unit.get("proposal"), dict) else {}
        proposal_id = str(proposal.get("proposal_id") or "").strip()
        if memory_id:
            refs.append({"type": "evolution_memory", "memory_id": memory_id})
        if proposal_id:
            refs.append({"type": "run_local_proposal", "proposal_id": proposal_id})
    return refs


def _proposal_validation_notes(*, site_summary: dict[str, Any], pattern_name: str) -> list[str]:
    notes: list[str] = []
    for unit in _memory_for_pattern(site_summary=site_summary, pattern_name=pattern_name):
        proposal = unit.get("proposal") if isinstance(unit.get("proposal"), dict) else {}
        proposal_id = str(proposal.get("proposal_id") or "").strip()
        proposal_status = str(proposal.get("proposal_status") or "").strip()
        materialized_type = str(proposal.get("materialized_change_type") or "").strip()
        if proposal_id:
            notes.append(
                f"proposal={proposal_id} status={proposal_status or 'unknown'} materialized={materialized_type or 'none'}."
            )
        usage_events = unit.get("usage_events") if isinstance(unit.get("usage_events"), list) else []
        validation_events = unit.get("validation_events") if isinstance(unit.get("validation_events"), list) else []
        if usage_events:
            notes.append(f"usage_events={len(usage_events)}.")
        for event in validation_events[-3:]:
            if not isinstance(event, dict):
                continue
            notes.append(
                "validation="
                f"{event.get('result') or 'unknown'} "
                f"job={event.get('job_id') or ''} "
                f"status={event.get('application_status') or ''} "
                f"pattern={event.get('failure_pattern') or ''}."
            )
    return notes[:8]


def _lesson_recommended_patterns(*, site_summary: dict[str, Any], pattern_name: str) -> list[str]:
    patterns = [
        "Use the batch workflow summary as evidence for Codex review; accept a durable lesson only after follow-up evidence confirms the pattern."
    ]
    proposal_notes = _proposal_validation_notes(site_summary=site_summary, pattern_name=pattern_name)
    if proposal_notes:
        patterns.append("Review the linked run-local proposal usage and validation before promoting this candidate to a durable Skill or accepted lesson.")
    for unit in _memory_for_pattern(site_summary=site_summary, pattern_name=pattern_name):
        for item in unit.get("recommended_patterns") or []:
            text = str(item or "").strip()
            if text and text not in patterns:
                patterns.append(text)
    return patterns[:8]


def _next_actions(site_summaries: list[dict[str, Any]]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for site in site_summaries:
        site_key = str(site.get("site_key") or "")
        for pattern in site.get("patterns") or []:
            if not isinstance(pattern, dict):
                continue
            action = str(pattern.get("action") or "")
            phase = str(pattern.get("phase") or "")
            name = str(pattern.get("pattern") or "")
            count = int(pattern.get("count") or 0)
            if action == "trigger_refinement":
                actions.append(
                    {
                        "type": "review_or_patch_site_skill",
                        "site_key": site_key,
                        "phase": phase,
                        "pattern": name,
                        "reason": f"{count} reusable loop-control refinement event(s) occurred in the batch.",
                    }
                )
    return actions


def _read_run_rows(*, workspace: Path, site_key: str, batch_id: str) -> list[dict[str, Any]]:
    path = workspace / "sites" / safe_file_stem(site_key) / "jobs" / "runs" / f"{safe_file_stem(batch_id)}.jsonl"
    return JSONLStore(path).read_all() if path.exists() else []


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Workflow Evolution Summary",
        "",
        f"- Batch: `{payload.get('batch_id')}`",
        f"- Status: `{payload.get('batch_status')}`",
        f"- Generated: {payload.get('generated_at')}",
        f"- Sites with loop evidence: {payload.get('sites_with_loop_evidence')}",
        f"- Lesson candidates written: {payload.get('lesson_candidates_written')}",
        "",
        "## Sites",
        "",
    ]
    decisions = payload.get("evolution_decisions") if isinstance(payload.get("evolution_decisions"), list) else []
    if decisions:
        lines.extend(["## Evolution Decisions", ""])
        for decision in decisions:
            if not isinstance(decision, dict):
                continue
            lines.append(
                f"- `{decision.get('decision_id')}` verdict=`{decision.get('verdict')}` "
                f"site=`{decision.get('site_key')}` phase=`{decision.get('phase')}` "
                f"pattern=`{decision.get('failure_pattern')}` target=`{decision.get('target_ref')}`"
            )
            plan = str(decision.get("validation_plan") or "").strip()
            if plan:
                lines.append(f"  Validation: {plan}")
        lines.append("")
    sites = payload.get("sites") if isinstance(payload.get("sites"), list) else []
    if not sites:
        lines.append("- No loop-control evidence in this batch.")
    for site in sites:
        lines.append(f"- `{site.get('site_key')}` status=`{site.get('site_status')}` patterns={site.get('loop_pattern_count')}")
        summary = str((site.get("run_context") or {}).get("apply_loop_refinement_summary") or "").strip()
        if summary:
            lines.append(f"  Guidance: {summary[:500]}")
        for pattern in site.get("patterns") or []:
            if not isinstance(pattern, dict):
                continue
            lines.append(
                f"  - `{pattern.get('phase')}` `{pattern.get('pattern')}` action=`{pattern.get('action')}` count={pattern.get('count')}"
            )
        memories = site.get("run_local_memory") if isinstance(site.get("run_local_memory"), list) else []
        if memories:
            lines.append("  Run-local evolution memory:")
            for unit in memories[-5:]:
                if not isinstance(unit, dict):
                    continue
                lines.append(f"  - `{unit.get('pattern')}` memory=`{unit.get('memory_id')}`")
                avoid = unit.get("avoid_patterns") if isinstance(unit.get("avoid_patterns"), list) else []
                recommended = unit.get("recommended_patterns") if isinstance(unit.get("recommended_patterns"), list) else []
                if avoid:
                    lines.append(f"    Avoid: {'; '.join(str(item) for item in avoid[:3])}")
                if recommended:
                    lines.append(f"    Prefer: {'; '.join(str(item) for item in recommended[:3])}")
                proposal = unit.get("proposal") if isinstance(unit.get("proposal"), dict) else {}
                if proposal:
                    lines.append(f"    Proposal: `{proposal.get('proposal_id')}` kind=`{proposal.get('proposal_kind')}`")
                validation_events = (
                    unit.get("validation_events") if isinstance(unit.get("validation_events"), list) else []
                )
                if validation_events:
                    lines.append("    Validation:")
                    for event in validation_events[-3:]:
                        if not isinstance(event, dict):
                            continue
                        lines.append(
                            f"    - `{event.get('result')}` job=`{event.get('job_id')}` status=`{event.get('application_status')}`"
                        )
    lines.extend(["", "## Next Actions", ""])
    actions = payload.get("next_actions") if isinstance(payload.get("next_actions"), list) else []
    if not actions:
        lines.append("- Keep observing.")
    for action in actions:
        lines.append(
            f"- `{action.get('type')}` site=`{action.get('site_key')}` phase=`{action.get('phase')}` pattern=`{action.get('pattern')}`: {action.get('reason')}"
        )
    return "\n".join(lines).rstrip() + "\n"
