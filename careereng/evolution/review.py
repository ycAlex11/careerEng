"""Build review-driven evolution evidence, candidates, and context packs."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from careereng.application_summary import build_application_summary
from careereng.evolution.schema import EvolutionEvidence, ImprovementCandidate, MemoryUnit
from careereng.evolution.store import EvolutionStore
from careereng.utils import now_iso, today_str


BROWSER_EVENT_TYPES = {
    "same_url_no_progress",
    "same_url_no_progress_tokens",
    "ignored_stop_recommended",
    "ignored_enrichment_required",
    "empty_extraction_loop",
}
APPLICATION_SUMMARY_UNMATCHED_REPAIR_THRESHOLD = 1
METRICS_ELAPSED_OUTLIER_MS = 10 * 60 * 1000
METRICS_TOKEN_OUTLIER = 100_000


def build_evolution_review(
    *,
    workspace: Path | str,
    project_root: Path | str | None = None,
    max_evidence: int = 200,
) -> dict[str, Any]:
    workspace_path = Path(workspace)
    root = Path(project_root) if project_root is not None else workspace_path.parent
    generated_at = now_iso()

    evidence = _dedupe_evidence(
        [
            *_browser_control_evidence(workspace_path),
            *_assistant_bridge_evidence(workspace_path),
            *_metrics_evidence(workspace_path),
            *_application_summary_evidence(workspace_path, project_root=root),
        ]
    )
    evidence = sorted(evidence, key=lambda item: (str(item.get("created_at") or ""), str(item.get("evidence_id") or "")))
    if max_evidence > 0:
        evidence = evidence[-max_evidence:]

    memory_units = _dedupe_memory_units(_memory_units_from_local_signals(workspace_path))
    candidates = _dedupe_candidates(_build_candidates(evidence=evidence, workspace=workspace_path, project_root=root))
    top_findings = _top_findings(evidence=evidence, candidates=candidates)

    return {
        "review_id": _stable_id("evo_review", {"date": today_str(), "workspace": str(workspace_path)}),
        "created_at": generated_at,
        "window": {"mode": "all_available", "max_evidence": max_evidence},
        "inputs": _input_paths(workspace_path),
        "evidence_count": len(evidence),
        "candidate_count": len(candidates),
        "memory_count": len(memory_units),
        "top_findings": top_findings,
        "open_candidates": [_candidate_brief(row) for row in candidates],
        "evidence": evidence,
        "memory_units": memory_units,
        "candidates": candidates,
        "context_pack_path": "",
    }


def save_evolution_review(review: dict[str, Any], *, workspace: Path | str) -> dict[str, Path]:
    workspace_path = Path(workspace)
    store = EvolutionStore(workspace_path)
    evidence = review.get("evidence") if isinstance(review.get("evidence"), list) else []
    candidates = review.get("candidates") if isinstance(review.get("candidates"), list) else []
    memory_units = review.get("memory_units") if isinstance(review.get("memory_units"), list) else []
    store.upsert_evidence([row for row in evidence if isinstance(row, dict)])
    store.upsert_open_candidates([row for row in candidates if isinstance(row, dict) and str(row.get("status") or "open") == "open"])
    store.upsert_memory_units([row for row in memory_units if isinstance(row, dict)])

    context_text = render_evolution_context(review)
    context_path = store.save_context_markdown(context_text)
    review_payload = {**review, "context_pack_path": str(_relative_path(context_path, workspace_path))}
    markdown_path = store.save_review_markdown(render_evolution_review_markdown(review_payload))
    json_path = store.save_review_json(review_payload)
    return {
        "review_json": json_path,
        "review_markdown": markdown_path,
        "context_markdown": context_path,
        "evidence_store": store.evidence_store.path,
        "open_candidates_store": store.open_candidates_store.path,
        "memory_units_store": store.memory_units_store.path,
    }


def render_evolution_review_markdown(review: dict[str, Any]) -> str:
    lines = [
        "# Evolution Review",
        "",
        f"- Review: `{review.get('review_id') or ''}`",
        f"- Created: {review.get('created_at') or ''}",
        f"- Evidence: {int(review.get('evidence_count') or 0)}",
        f"- Open candidates: {int(review.get('candidate_count') or 0)}",
        f"- Memory units: {int(review.get('memory_count') or 0)}",
        f"- Context pack: `{review.get('context_pack_path') or 'workspace/evolution/context/latest.md'}`",
        "",
        "## Top Findings",
    ]
    findings = review.get("top_findings") if isinstance(review.get("top_findings"), list) else []
    if not findings:
        lines.append("- No high-signal evolution evidence found.")
    else:
        for item in findings[:10]:
            lines.append(f"- {item}")

    lines.extend(["", "## Open Candidates"])
    candidates = review.get("candidates") if isinstance(review.get("candidates"), list) else []
    if not candidates:
        lines.append("- None")
    else:
        for row in candidates[:20]:
            lines.append(
                f"- [{row.get('priority') or 'medium'}] {row.get('summary') or row.get('candidate_id')} "
                f"-> `{row.get('target_ref') or row.get('target_type') or ''}`"
            )

    lines.extend(["", "## Recent Evidence"])
    evidence = review.get("evidence") if isinstance(review.get("evidence"), list) else []
    if not evidence:
        lines.append("- None")
    else:
        for row in evidence[-20:]:
            parts = [
                str(row.get("area") or "unknown"),
                str(row.get("site_key") or "").strip(),
                str(row.get("phase") or "").strip(),
                str(row.get("event_type") or "").strip(),
            ]
            prefix = " / ".join(part for part in parts if part)
            lines.append(f"- `{prefix}` {row.get('summary') or row.get('evidence_id')}")
    return "\n".join(lines)


def render_evolution_context(review: dict[str, Any]) -> str:
    lines = [
        "# CareerEng Evolution Context",
        "",
        "This file is a compact context pack for future assistant review. Treat it as evidence-backed guidance, not an instruction to modify files automatically.",
        "",
        "## High-Signal Evidence",
    ]
    evidence = review.get("evidence") if isinstance(review.get("evidence"), list) else []
    high_signal = [row for row in evidence if str(row.get("severity") or "") in {"high", "medium"}]
    if not high_signal:
        lines.append("- None")
    else:
        for row in high_signal[-12:]:
            lines.append(
                f"- {row.get('summary') or row.get('event_type')} "
                f"(area={row.get('area') or '-'}, site={row.get('site_key') or '-'}, phase={row.get('phase') or '-'})"
            )

    lines.extend(["", "## Open Improvement Candidates"])
    candidates = review.get("candidates") if isinstance(review.get("candidates"), list) else []
    if not candidates:
        lines.append("- None")
    else:
        for row in candidates[:12]:
            lines.append(f"- {row.get('summary')}: {row.get('suggested_change')}")

    lines.extend(["", "## Durable Memory Units"])
    memory_units = review.get("memory_units") if isinstance(review.get("memory_units"), list) else []
    if not memory_units:
        lines.append("- None")
    else:
        for row in memory_units[-12:]:
            labels = ", ".join(str(item) for item in row.get("labels") or [])
            lines.append(f"- [{row.get('memory_type')}] {row.get('summary')} ({labels or 'no labels'})")

    lines.extend(["", "## Human Decisions Needed"])
    if not candidates:
        lines.append("- No immediate evolution decision is needed.")
    else:
        lines.append("- Review open candidates before changing Skills, config, or runtime code.")
        lines.append("- Prefer skill/config changes before Python runtime changes unless evidence points to host-layer failure.")
    return "\n".join(lines)


def _browser_control_evidence(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / "evolution" / "browser_control" / "phase_events.jsonl"
    rows: list[dict[str, Any]] = []
    for idx, row in _read_jsonl_with_refs(path):
        event_type = str(row.get("event_type") or "").strip()
        if event_type not in BROWSER_EVENT_TYPES:
            continue
        site_key = _text(row.get("site_key"))
        phase = _text(row.get("phase"))
        guard_name = _text(row.get("guard_name"))
        severity = "high" if event_type in {"same_url_no_progress", "same_url_no_progress_tokens", "empty_extraction_loop"} else "medium"
        summary = _text(row.get("summary")) or f"{site_key or 'site'} {phase or 'phase'} triggered {guard_name or event_type}"
        rows.append(
            _evidence(
                created_at=_text(row.get("created_at")),
                source_type="browser_control_phase_event",
                source_ref=_source_ref(workspace, path, idx),
                area="browser_control",
                site_key=site_key,
                phase=phase,
                event_type=event_type,
                severity=severity,
                summary=summary,
                details={
                    "batch_id": _text(row.get("batch_id")),
                    "turn_id": _text(row.get("turn_id")),
                    "current_url": _text(row.get("current_url")),
                    "guard_name": guard_name,
                    "trigger_values": row.get("trigger_values") if isinstance(row.get("trigger_values"), dict) else {},
                    "last_record_jobs_policy": row.get("last_record_jobs_policy")
                    if isinstance(row.get("last_record_jobs_policy"), dict)
                    else {},
                    "trace_ref": _text(row.get("trace_ref")),
                },
                entities={"site_key": site_key, "phase": phase, "guard_name": guard_name},
                tags=["browser_control", event_type, guard_name],
            )
        )
    return rows


def _assistant_bridge_evidence(workspace: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    correction_path = workspace / "assistant_bridge" / "correction_events.jsonl"
    for idx, row in _read_jsonl_with_refs(correction_path):
        text = _text(row.get("user_correction"))
        rows.append(
            _evidence(
                created_at=_text(row.get("created_at")),
                source_type="assistant_correction_event",
                source_ref=_source_ref(workspace, correction_path, idx),
                area="assistant_router",
                event_type="routing_correction",
                severity="medium",
                summary=text[:180] if text else "Assistant routing/action was corrected by the user.",
                details=row,
                entities={},
                tags=["assistant_bridge", "routing_correction"],
            )
        )
    routing_path = workspace / "assistant_bridge" / "routing_examples.jsonl"
    for idx, row in _read_jsonl_with_refs(routing_path):
        if _text(row.get("label_source")) != "correction":
            continue
        text = _text(row.get("text"))
        rows.append(
            _evidence(
                created_at=_text(row.get("created_at")),
                source_type="assistant_routing_example",
                source_ref=_source_ref(workspace, routing_path, idx),
                area="assistant_router",
                event_type="routing_example_from_correction",
                severity="medium",
                summary=f"Routing correction example: {text[:140]}" if text else "Routing correction example recorded.",
                details=row,
                entities=row.get("detected_entities") if isinstance(row.get("detected_entities"), dict) else {},
                tags=["assistant_bridge", "routing_example", "correction"],
            )
        )
    return rows


def _metrics_evidence(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / "metrics" / "llm_usage.jsonl"
    metrics_rows = [row for _, row in _read_jsonl_with_refs(path)]
    evidence_rows: list[dict[str, Any]] = []
    for idx, row in _read_jsonl_with_refs(path):
        if _text(row.get("status")) == "ok":
            continue
        site_key = _text(row.get("site_key") or row.get("site_id"))
        phase = _text(row.get("phase"))
        evidence_rows.append(
            _evidence(
                created_at=_text(row.get("ts") or row.get("created_at")),
                source_type="metrics_usage_row",
                source_ref=_source_ref(workspace, path, idx),
                area="metrics",
                site_key=site_key,
                phase=phase,
                event_type="metrics_error",
                severity="high",
                summary=f"LLM usage call failed for {site_key or 'unknown site'} {phase or 'unknown phase'}",
                details=row,
                entities={"site_key": site_key, "phase": phase, "model": _text(row.get("model"))},
                tags=["metrics", "error"],
            )
        )

    grouped: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"elapsed_ms": 0, "total_tokens": 0, "calls": 0})
    for row in metrics_rows:
        key = (_text(row.get("site_key") or row.get("site_id")), _text(row.get("phase")))
        grouped[key]["elapsed_ms"] += _int(row.get("elapsed_ms"))
        grouped[key]["total_tokens"] += _int(row.get("total_tokens"))
        grouped[key]["calls"] += 1
    for (site_key, phase), totals in grouped.items():
        elapsed = totals["elapsed_ms"]
        tokens = totals["total_tokens"]
        if elapsed < METRICS_ELAPSED_OUTLIER_MS and tokens < METRICS_TOKEN_OUTLIER:
            continue
        evidence_rows.append(
            _evidence(
                created_at=now_iso(),
                source_type="metrics_aggregate",
                source_ref=_source_ref(workspace, path, 0),
                area="metrics",
                site_key=site_key,
                phase=phase,
                event_type="cost_outlier",
                severity="medium",
                summary=(
                    f"High runtime/usage for {site_key or 'unknown site'} {phase or 'unknown phase'}: "
                    f"{elapsed}ms, {tokens} tokens across {totals['calls']} calls"
                ),
                details=totals,
                entities={"site_key": site_key, "phase": phase},
                tags=["metrics", "cost_outlier"],
            )
        )
    return evidence_rows


def _application_summary_evidence(workspace: Path, *, project_root: Path) -> list[dict[str, Any]]:
    try:
        summary = build_application_summary(workspace=workspace, project_root=project_root)
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for transition in summary.get("lifecycle_transitions") or []:
        if not isinstance(transition, dict):
            continue
        site_key = _text(transition.get("site_key"))
        title = _text(transition.get("title"))
        current_status = _text(transition.get("current_status") or transition.get("current_stage"))
        previous_status = _text(transition.get("previous_status") or transition.get("previous_stage"))
        transition_type = _text(transition.get("transition_type")) or "status_changed"
        severity = "high" if "to_rejected" in transition_type else "medium"
        rows.append(
            _evidence(
                created_at=_text(transition.get("checked_at")),
                source_type="application_summary_transition",
                source_ref="application_summary/lifecycle_transitions",
                area="application_status",
                site_key=site_key,
                event_type="application_status_change",
                severity=severity,
                summary=f"{site_key or 'site'} status changed for {title or 'unknown job'}: {previous_status or '-'} -> {current_status or '-'}",
                details=transition,
                entities={
                    "site_key": site_key,
                    "title": title,
                    "site_job_id": _text(transition.get("site_job_id")),
                    "transition_type": transition_type,
                },
                tags=["application_status", transition_type],
            )
        )
    unmatched = summary.get("unmatched_reviews") if isinstance(summary.get("unmatched_reviews"), list) else []
    for item in unmatched:
        if not isinstance(item, dict):
            continue
        site_key = _text(item.get("site_key"))
        title = _text(item.get("title"))
        rows.append(
            _evidence(
                created_at=_text(item.get("checked_at") or item.get("latest_seen_at")),
                source_type="application_summary_unmatched_review",
                source_ref="application_summary/unmatched_reviews",
                area="history_repair",
                site_key=site_key,
                event_type="unmatched_review",
                severity="medium",
                summary=f"{site_key or 'site'} has unmatched dashboard review for {title or 'unknown job'}",
                details=item,
                entities={"site_key": site_key, "title": title, "site_job_id": _text(item.get("site_job_id"))},
                tags=["history_repair", "unmatched_review"],
            )
        )
    return rows


def _memory_units_from_local_signals(workspace: Path) -> list[dict[str, Any]]:
    unified_units_path = workspace / "memory" / "memory_units.jsonl"
    specs = [
        ("profile_resume_signal", workspace / "memory" / "profile_signals.jsonl"),
        ("career_intent_strategy", workspace / "memory" / "intent_signals.jsonl"),
        ("application_feedback", workspace / "memory" / "application_feedback_signals.jsonl"),
        ("interview_record", workspace / "interviews" / "events.jsonl"),
    ]
    units: list[dict[str, Any]] = []
    for memory_type, path in specs:
        for idx, row in _read_jsonl_with_refs(path):
            text = _text(row.get("source_text") or row.get("content") or row.get("evidence"))
            if not text:
                continue
            entities = row.get("detected_entities") if isinstance(row.get("detected_entities"), dict) else {}
            labels = [str(item) for item in row.get("semantic_labels") or [] if str(item).strip()]
            units.append(
                _memory_unit(
                    created_at=_text(row.get("created_at")),
                    memory_type=memory_type,
                    status=_text(row.get("status")) or "raw",
                    summary=_truncate(text, 180),
                    content=text,
                    entities=entities,
                    labels=labels,
                    source_refs=[_source_ref(workspace, path, idx)],
                    confidence=float(row.get("confidence") or 0.0),
                )
            )
    for idx, row in _read_jsonl_with_refs(unified_units_path):
        memory_type = _text(row.get("category"))
        summary = _text(row.get("summary"))
        content = _text(row.get("source_text")) or summary
        if not memory_type or not (summary or content):
            continue
        entities = row.get("entities") if isinstance(row.get("entities"), dict) else {}
        labels = [str(item) for item in row.get("tags") or [] if str(item).strip()]
        units.append(
            _memory_unit(
                created_at=_text(row.get("created_at")),
                memory_type=memory_type,
                status=_text(row.get("status")) or "active",
                summary=_truncate(summary or content, 180),
                content=content,
                entities=entities,
                labels=labels,
                source_refs=[_source_ref(workspace, unified_units_path, idx)],
                confidence=float(row.get("confidence") or 0.0),
            )
        )
    return units


def _build_candidates(*, evidence: list[dict[str, Any]], workspace: Path, project_root: Path) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in evidence:
        grouped[
            (
                _text(row.get("area")),
                _text(row.get("site_key")),
                _text(row.get("phase")),
                _text(row.get("event_type")),
            )
        ].append(row)

    candidates: list[dict[str, Any]] = []
    for (area, site_key, phase, event_type), rows in grouped.items():
        evidence_ids = sorted({_text(row.get("evidence_id")) for row in rows if _text(row.get("evidence_id"))})
        if area == "browser_control":
            candidates.append(_browser_candidate(site_key=site_key, phase=phase, event_type=event_type, rows=rows, evidence_ids=evidence_ids))
        elif area == "assistant_router":
            candidates.append(_assistant_router_candidate(rows=rows, evidence_ids=evidence_ids))
        elif area == "history_repair" and len(rows) >= APPLICATION_SUMMARY_UNMATCHED_REPAIR_THRESHOLD:
            candidates.append(_history_repair_candidate(rows=rows, evidence_ids=evidence_ids))
        elif area == "metrics":
            candidates.append(_metrics_candidate(site_key=site_key, phase=phase, rows=rows, evidence_ids=evidence_ids))

    # Application status transitions are evidence for future strategy, but v1 does not automatically propose strategy changes.
    return [row for row in candidates if row]


def _browser_candidate(*, site_key: str, phase: str, event_type: str, rows: list[dict[str, Any]], evidence_ids: list[str]) -> dict[str, Any]:
    target_ref = f"skills/search/jobs/sites/{site_key}/SKILL.md" if site_key else "skills/search/jobs/SKILL.md"
    priority = "high" if event_type in {"same_url_no_progress", "same_url_no_progress_tokens", "empty_extraction_loop"} else "medium"
    suggested_change = (
        f"Review and clarify the {phase or 'affected'} workflow instructions so the browser agent records progress, "
        "does not revisit the same page unnecessarily, and obeys retrieval stop/enrichment signals."
    )
    return _candidate(
        area="site_skill" if site_key else "project_skill",
        target_type="site_skill" if site_key else "project_skill",
        target_ref=target_ref,
        site_key=site_key,
        phase=phase,
        priority=priority,
        summary=f"{site_key or 'Project'} {phase or 'workflow'} triggered {event_type} {len(rows)} time(s)",
        suggested_change=suggested_change,
        reason="Browser-control guard events indicate the current instructions or flow may not produce stable progress.",
        evidence_ids=evidence_ids,
        risk="medium",
    )


def _assistant_router_candidate(*, rows: list[dict[str, Any]], evidence_ids: list[str]) -> dict[str, Any]:
    return _candidate(
        area="assistant_router",
        target_type="assistant_router",
        target_ref="careereng/integrations/assistant_bridge/processors/local.py",
        priority="medium",
        summary=f"Assistant routing has {len(rows)} correction-related evidence item(s)",
        suggested_change="Review correction examples and update routing examples or processor rules if the same pattern repeats.",
        reason="User corrections are direct evidence that assistant routing or command selection may be wrong.",
        evidence_ids=evidence_ids,
        risk="low",
    )


def _history_repair_candidate(*, rows: list[dict[str, Any]], evidence_ids: list[str]) -> dict[str, Any]:
    sites = sorted({_text(row.get("site_key")) for row in rows if _text(row.get("site_key"))})
    return _candidate(
        area="history_repair",
        target_type="data_repair",
        target_ref="python -m careereng application-summary repair-history",
        site_key=", ".join(sites),
        priority="high" if len(rows) >= 5 else "medium",
        summary=f"{len(rows)} unmatched application review record(s) need history enrichment or repair",
        suggested_change="Run the history repair flow, then enrich true unmatched jobs during retrieval when the same jobs are seen again.",
        reason="Unmatched dashboard reviews reduce status tracking quality and application summary accuracy.",
        evidence_ids=evidence_ids,
        risk="low",
    )


def _metrics_candidate(*, site_key: str, phase: str, rows: list[dict[str, Any]], evidence_ids: list[str]) -> dict[str, Any]:
    return _candidate(
        area="runtime_config",
        target_type="runtime_config",
        target_ref="config.toml [browser.budgets] / provider settings",
        site_key=site_key,
        phase=phase,
        priority="medium",
        summary=f"{site_key or 'Unknown site'} {phase or 'unknown phase'} has metrics error or cost outlier evidence",
        suggested_change="Inspect metrics rows before changing budgets; prefer skill fixes when cost is caused by repeated browser loops.",
        reason="Runtime and token usage outliers are candidates for skill, timeout, or provider configuration review.",
        evidence_ids=evidence_ids,
        risk="medium",
    )


def _evidence(
    *,
    created_at: str,
    source_type: str,
    source_ref: str,
    area: str,
    site_key: str = "",
    phase: str = "",
    event_type: str,
    severity: str,
    summary: str,
    details: dict[str, Any],
    entities: dict[str, Any],
    tags: list[str],
) -> dict[str, Any]:
    fingerprint_payload = {
        "source_type": source_type,
        "source_ref": source_ref,
        "area": area,
        "site_key": site_key,
        "phase": phase,
        "event_type": event_type,
        "summary": summary,
    }
    fingerprint = _fingerprint(fingerprint_payload)
    return EvolutionEvidence(
        evidence_id=_stable_id("evidence", fingerprint_payload),
        created_at=created_at or now_iso(),
        source_type=source_type,
        source_ref=source_ref,
        area=area,
        site_key=site_key,
        phase=phase,
        event_type=event_type,
        severity=severity,
        summary=summary,
        details=details,
        entities=entities,
        tags=_clean_tags(tags),
        fingerprint=fingerprint,
    ).to_dict()


def _candidate(
    *,
    area: str,
    target_type: str,
    target_ref: str,
    summary: str,
    suggested_change: str,
    reason: str,
    evidence_ids: list[str],
    site_key: str = "",
    phase: str = "",
    priority: str = "medium",
    risk: str = "medium",
) -> dict[str, Any]:
    fingerprint_payload = {
        "area": area,
        "target_type": target_type,
        "target_ref": target_ref,
        "site_key": site_key,
        "phase": phase,
        "summary": summary,
    }
    fingerprint = _fingerprint(fingerprint_payload)
    now = now_iso()
    return ImprovementCandidate(
        candidate_id=_stable_id("candidate", fingerprint_payload),
        created_at=now,
        updated_at=now,
        area=area,
        target_type=target_type,
        target_ref=target_ref,
        site_key=site_key,
        phase=phase,
        priority=priority,
        status="open",
        summary=summary,
        suggested_change=suggested_change,
        reason=reason,
        evidence_ids=evidence_ids,
        evidence_count=len(evidence_ids),
        risk=risk,
        owner="human",
        fingerprint=fingerprint,
    ).to_dict()


def _memory_unit(
    *,
    created_at: str,
    memory_type: str,
    status: str,
    summary: str,
    content: str,
    entities: dict[str, Any],
    labels: list[str],
    source_refs: list[str],
    confidence: float,
) -> dict[str, Any]:
    fingerprint_payload = {
        "memory_type": memory_type,
        "summary": summary,
        "source_refs": source_refs,
    }
    fingerprint = _fingerprint(fingerprint_payload)
    return MemoryUnit(
        memory_id=_stable_id("memory", fingerprint_payload),
        created_at=created_at or now_iso(),
        updated_at=now_iso(),
        memory_type=memory_type,
        status=status,
        summary=summary,
        content=content,
        entities=entities,
        labels=labels,
        source_refs=source_refs,
        confidence=confidence,
        supersedes=[],
        fingerprint=fingerprint,
    ).to_dict()


def _read_jsonl_with_refs(path: Path) -> list[tuple[int, dict[str, Any]]]:
    if not path.exists():
        return []
    rows: list[tuple[int, dict[str, Any]]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for idx, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
        if isinstance(data, dict):
            rows.append((idx, data))
    return rows


def _dedupe_evidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _dedupe_rows(rows, "evidence_id")


def _dedupe_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(_dedupe_rows(rows, "candidate_id"), key=lambda row: (_priority_rank(row.get("priority")), str(row.get("summary") or "")))


def _dedupe_memory_units(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _dedupe_rows(rows, "memory_id")


def _dedupe_rows(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_key = _text(row.get(key))
        if not row_key:
            continue
        if row_key in by_key and key == "candidate_id":
            current_ids = set(by_key[row_key].get("evidence_ids") or [])
            current_ids.update(str(item) for item in row.get("evidence_ids") or [] if str(item).strip())
            by_key[row_key]["evidence_ids"] = sorted(current_ids)
            by_key[row_key]["evidence_count"] = len(current_ids)
            by_key[row_key]["updated_at"] = now_iso()
        else:
            by_key[row_key] = row
    return list(by_key.values())


def _top_findings(*, evidence: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> list[str]:
    findings: list[str] = []
    high = [row for row in evidence if str(row.get("severity") or "") == "high"]
    if high:
        findings.append(f"{len(high)} high-severity evidence item(s) were found.")
    counter = Counter(str(row.get("area") or "unknown") for row in evidence)
    for area, count in counter.most_common(5):
        findings.append(f"{area}: {count} evidence item(s).")
    if candidates:
        findings.append(f"{len(candidates)} open improvement candidate(s) were generated.")
    return findings[:10]


def _candidate_brief(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": row.get("candidate_id"),
        "priority": row.get("priority"),
        "area": row.get("area"),
        "target_ref": row.get("target_ref"),
        "summary": row.get("summary"),
        "evidence_count": row.get("evidence_count"),
    }


def _input_paths(workspace: Path) -> dict[str, str]:
    return {
        "browser_control_events": str(Path("evolution") / "browser_control" / "phase_events.jsonl"),
        "assistant_routing_examples": str(Path("assistant_bridge") / "routing_examples.jsonl"),
        "assistant_corrections": str(Path("assistant_bridge") / "correction_events.jsonl"),
        "career_memory_units": str(Path("memory") / "memory_units.jsonl"),
        "metrics_usage": str(Path("metrics") / "llm_usage.jsonl"),
        "site_history": str(Path("sites") / "*" / "jobs" / "history_jobs.json"),
        "workspace": str(workspace),
    }


def _source_ref(workspace: Path, path: Path, idx: int) -> str:
    rel = _relative_path(path, workspace)
    return f"{rel}#{idx}" if idx > 0 else str(rel)


def _relative_path(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError:
        return path


def _stable_id(prefix: str, payload: Any) -> str:
    return f"{prefix}_{_fingerprint(payload)[:12]}"


def _fingerprint(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _truncate(value: str, limit: int) -> str:
    text = _text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _clean_tags(tags: list[str]) -> list[str]:
    cleaned = []
    for item in tags:
        text = _text(item)
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _priority_rank(value: Any) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(_text(value), 3)
