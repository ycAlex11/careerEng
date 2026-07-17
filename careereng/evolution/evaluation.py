"""Evaluate applied evolution runs and select retention status."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from careereng.evolution.work_items import ActionCardStore
from careereng.evolution.reviews import build_assistant_memory_review_pack, save_review_pack
from careereng.career.applications.job_store import JobStore
from careereng.platform.persistence import JSONLStore
from careereng.utils import ensure_dir, now_iso, read_json, write_json


DEFAULT_RECENT_LIMIT = 10
MIN_FOLLOWUP_OBSERVATIONS = 3
NEGATIVE_BROWSER_EVENTS = {
    "same_url_no_progress",
    "same_url_no_progress_tokens",
    "empty_extraction_loop",
    "retrieval_stop_recommended",
    "retrieval_enrichment_required",
}


class EvolutionEvaluationError(ValueError):
    """Raised when an evolution run cannot be evaluated."""


def evaluate_evolution_run(
    *,
    workspace: Path | str,
    run_id: str,
    project_root: Path | str,
    recent_limit: int = DEFAULT_RECENT_LIMIT,
) -> dict[str, Any]:
    workspace_path = Path(workspace)
    root = Path(project_root)
    run_dir = workspace_path / "evolution" / "runs" / str(run_id or "").strip()
    run_path = run_dir / "run.json"
    run_payload = read_json(run_path)
    if not run_payload:
        raise EvolutionEvaluationError(f"Unknown evolution run: {run_id}")
    candidate_id = str(run_payload.get("candidate_id") or "")
    run_status = str(run_payload.get("status") or "")
    if candidate_id == "assistant_router_memory_intake":
        if run_status not in {"created", "applied", "evaluated"}:
            raise EvolutionEvaluationError(
                "Assistant router memory intake review can only run from created, applied, or evaluated status."
            )
        applied_at = str(run_payload.get("updated_at") or run_payload.get("created_at") or "") if run_status == "applied" else ""
        return _evaluate_assistant_router_memory_intake(
            workspace=workspace_path,
            run_dir=run_dir,
            run_payload=run_payload,
            applied_at=applied_at,
            recent_limit=recent_limit,
        )
    if run_status != "applied":
        raise EvolutionEvaluationError("Only applied evolution runs can be evaluated.")

    applied_files_path = _resolve_run_path(run_dir, run_payload.get("outputs", {}).get("applied_files"))
    applied_payload = read_json(applied_files_path) if applied_files_path else {}
    applied_at = str(applied_payload.get("applied_at") or run_payload.get("updated_at") or run_payload.get("created_at") or "")
    applied_files = applied_payload.get("files") if isinstance(applied_payload.get("files"), list) else []
    site_key = _infer_site_key(run_payload=run_payload, applied_files=applied_files)
    phase = _infer_primary_phase(run_payload)

    metrics = _compare_metrics(
        workspace=workspace_path,
        applied_at=applied_at,
        site_key=site_key,
        phase=phase,
        recent_limit=recent_limit,
    )
    browser_events = _compare_browser_events(
        workspace=workspace_path,
        applied_at=applied_at,
        site_key=site_key,
        phase=phase,
        recent_limit=recent_limit,
    )
    batches = _compare_batches(
        workspace=workspace_path,
        applied_at=applied_at,
        site_key=site_key,
        recent_limit=recent_limit,
    )

    selection = _select_result(metrics=metrics, browser_events=browser_events, batches=batches)
    now = now_iso()
    evaluation_payload: dict[str, Any] = {
        "run_id": run_payload.get("run_id"),
        "candidate_id": run_payload.get("candidate_id"),
        "evaluated_at": now,
        "applied_at": applied_at,
        "scope": {
            "site_key": site_key,
            "phase": phase,
            "recent_limit": max(1, int(recent_limit or DEFAULT_RECENT_LIMIT)),
        },
        "metrics": metrics,
        "browser_events": browser_events,
        "batches": batches,
        "selection": selection,
    }

    evaluations_dir = ensure_dir(run_dir / "evaluations")
    retention_dir = ensure_dir(run_dir / "retention")
    evaluation_json = evaluations_dir / "evaluation.json"
    evaluation_markdown = evaluations_dir / "evaluation.md"
    selection_json = retention_dir / "selection.json"
    write_json(evaluation_json, evaluation_payload)
    evaluation_markdown.write_text(_render_evaluation_markdown(evaluation_payload), encoding="utf-8")
    write_json(selection_json, selection)

    outputs = run_payload.setdefault("outputs", {})
    outputs["evaluation"] = str(evaluation_json)
    outputs["retention"] = str(selection_json)
    run_payload["updated_at"] = now
    evaluation_state = run_payload.setdefault("evaluation", {})
    evaluation_state.update(
        {
            "status": "completed",
            "evaluated_at": now,
            "evaluation_json": str(evaluation_json),
            "evaluation_markdown": str(evaluation_markdown),
            "selection_status": selection["status"],
        }
    )
    run_payload["selection"] = dict(selection)
    lifecycle = run_payload.setdefault("lifecycle", [])
    if isinstance(lifecycle, list):
        lifecycle.append(
            {
                "status": "evaluated",
                "at": now,
                "summary": f"Evaluation selected {selection['status']}.",
            }
        )
    write_json(run_path, run_payload)
    _update_summary(run_dir=run_dir, run_payload=run_payload, evaluation=evaluation_payload)

    return {
        "run_id": run_payload.get("run_id"),
        "status": "evaluated",
        "selection": selection["status"],
        "evaluation": evaluation_json,
        "evaluation_markdown": evaluation_markdown,
        "selection_json": selection_json,
        "summary": run_dir / "summary.md",
    }


def _evaluate_assistant_router_memory_intake(
    *,
    workspace: Path,
    run_dir: Path,
    run_payload: dict[str, Any],
    applied_at: str,
    recent_limit: int,
) -> dict[str, Any]:
    now = now_iso()
    evaluations_dir = ensure_dir(run_dir / "evaluations")
    retention_dir = ensure_dir(run_dir / "retention")
    review_pack = build_assistant_memory_review_pack(
        workspace=workspace,
        run_payload=run_payload,
        applied_at=applied_at,
        sample_limit=max(1, int(recent_limit or DEFAULT_RECENT_LIMIT)),
    )
    review_paths = save_review_pack(review_pack, output_dir=evaluations_dir)
    selection = {
        "status": "needs_codex_review",
        "decision_at": now,
        "reason": "assistant router and career-memory intake review requires Codex-assisted human judgment",
        "confidence": "manual_review_required",
        "followup_observations": int(review_pack.metrics.get("intake_count") or 0)
        + int(review_pack.metrics.get("memory_unit_count") or 0)
        + int(review_pack.metrics.get("correction_count") or 0),
        "rollback_recommended": False,
    }
    evaluation_payload: dict[str, Any] = {
        "run_id": run_payload.get("run_id"),
        "candidate_id": run_payload.get("candidate_id"),
        "evaluated_at": now,
        "applied_at": applied_at,
        "scope": {
            "review_type": "assistant_router_memory_intake",
            "recent_limit": max(1, int(recent_limit or DEFAULT_RECENT_LIMIT)),
        },
        "metrics": review_pack.metrics,
        "review": {
            "review_id": review_pack.review_id,
            "status": review_pack.status,
            "recommended_status": review_pack.recommended_status,
            "codex_review_required": review_pack.codex_review_required,
            "review_pack": str(review_paths["markdown"]),
            "review_pack_json": str(review_paths["json"]),
            "review_questions": list(review_pack.review_questions),
        },
        "selection": selection,
    }

    evaluation_json = evaluations_dir / "evaluation.json"
    evaluation_markdown = evaluations_dir / "evaluation.md"
    selection_json = retention_dir / "selection.json"
    write_json(evaluation_json, evaluation_payload)
    evaluation_markdown.write_text(_render_review_only_evaluation_markdown(evaluation_payload), encoding="utf-8")
    write_json(selection_json, selection)
    action_card = _create_assistant_memory_review_action_card(
        workspace=workspace,
        run_payload=run_payload,
        review_paths=review_paths,
        evaluation_json=evaluation_json,
        selection_json=selection_json,
    )
    if action_card:
        action_card_path = workspace / str(action_card.get("markdown_path") or "")
        evaluation_payload["review"]["action_card_id"] = action_card.get("card_id") or ""
        evaluation_payload["review"]["action_card"] = str(action_card_path)
        write_json(evaluation_json, evaluation_payload)
        evaluation_markdown.write_text(_render_review_only_evaluation_markdown(evaluation_payload), encoding="utf-8")

    outputs = run_payload.setdefault("outputs", {})
    outputs["evaluation"] = str(evaluation_json)
    outputs["retention"] = str(selection_json)
    outputs["codex_review_pack"] = str(review_paths["markdown"])
    outputs["review_pack_json"] = str(review_paths["json"])
    if action_card:
        outputs["action_card"] = str(workspace / str(action_card.get("markdown_path") or ""))
    run_payload["updated_at"] = now
    evaluation_state = run_payload.setdefault("evaluation", {})
    evaluation_state.update(
        {
            "status": "completed",
            "evaluated_at": now,
            "evaluation_json": str(evaluation_json),
            "evaluation_markdown": str(evaluation_markdown),
            "selection_status": selection["status"],
            "codex_review_required": True,
            "codex_review_pack": str(review_paths["markdown"]),
            "action_card_id": action_card.get("card_id") if action_card else "",
            "action_card": str(workspace / str(action_card.get("markdown_path") or "")) if action_card else "",
        }
    )
    run_payload["selection"] = dict(selection)
    lifecycle = run_payload.setdefault("lifecycle", [])
    if isinstance(lifecycle, list):
        lifecycle.append(
            {
                "status": "evaluated",
                "at": now,
                "summary": "Assistant memory intake evaluation prepared a Codex review pack.",
            }
        )
    write_json(run_dir / "run.json", run_payload)
    _update_summary(run_dir=run_dir, run_payload=run_payload, evaluation=evaluation_payload)

    return {
        "run_id": run_payload.get("run_id"),
        "status": "evaluated",
        "selection": selection["status"],
        "evaluation": evaluation_json,
        "evaluation_markdown": evaluation_markdown,
        "selection_json": selection_json,
        "review_pack": review_paths["markdown"],
        "action_card": Path(str(outputs.get("action_card"))) if outputs.get("action_card") else None,
        "summary": run_dir / "summary.md",
    }


def _create_assistant_memory_review_action_card(
    *,
    workspace: Path,
    run_payload: dict[str, Any],
    review_paths: dict[str, Path],
    evaluation_json: Path,
    selection_json: Path,
) -> dict[str, Any]:
    run_id = str(run_payload.get("run_id") or "")
    return ActionCardStore(workspace).create_card(
        card_type="codex_review",
        title="Review assistant memory intake",
        goal=(
            "Review the generated assistant memory intake pack and recommend whether the local "
            "routing/memory behavior should be accepted, observed longer, rejected, or rolled back."
        ),
        reason="Assistant-router memory intake evaluation requires Codex-assisted human judgment.",
        source_type="evolution_run",
        source_id=run_id,
        source_ref=str(review_paths["markdown"]),
        priority="medium",
        related_files=[
            str(review_paths["markdown"]),
            str(review_paths["json"]),
            str(evaluation_json),
            str(selection_json),
            str(workspace / "memory" / "memory_units.jsonl"),
            str(workspace / "assistant_bridge" / "intake_events.jsonl"),
            str(workspace / "assistant_bridge" / "routing_examples.jsonl"),
            str(workspace / "assistant_bridge" / "correction_events.jsonl"),
        ],
        suggested_actions=[
            "Read the Codex review pack first.",
            "Inspect sampled intake events, memory units, routing examples, and corrections only as needed.",
            "Recommend one status: accepted, keep_observing, low_confidence, rejected, or rollback_recommended.",
            "Close this card with a concise review result after the recommendation is recorded.",
        ],
        safety_notes=[
            "Do not evaluate generic model quality; evaluate CareerEng local behavior and stored evidence.",
            "Do not modify files while doing the review unless the user explicitly asks for implementation.",
            "Do not auto-promote action-card content into career memory in v1.",
        ],
        done_when=[
            "A review recommendation is written in the conversation or local notes.",
            "The card is closed with a result summary.",
        ],
        metadata={
            "candidate_id": str(run_payload.get("candidate_id") or ""),
            "selection_status": "needs_codex_review",
            "review_pack": str(review_paths["markdown"]),
        },
        semantic_tags=[
            "assistant_memory",
            "router_memory_intake",
            "codex_review",
            "career_memory",
            "evolution_evaluation",
        ],
        dedupe_key=f"codex_review:evolution_run:{run_id}",
    )


def _compare_metrics(
    *,
    workspace: Path,
    applied_at: str,
    site_key: str,
    phase: str,
    recent_limit: int,
) -> dict[str, Any]:
    rows = _read_jsonl(workspace / "metrics" / "llm_usage.jsonl")
    filtered = [
        row
        for row in rows
        if _matches_scope(row, site_key=site_key, phase=phase)
    ]
    before = [row for row in filtered if _is_before(str(row.get("ts") or row.get("created_at") or ""), applied_at)]
    after = [row for row in filtered if _is_after_or_equal(str(row.get("ts") or row.get("created_at") or ""), applied_at)]
    return {
        "source": str(workspace / "metrics" / "llm_usage.jsonl"),
        "before": _metric_totals(before[-_positive_limit(recent_limit) :]),
        "after": _metric_totals(after[-_positive_limit(recent_limit) :]),
    }


def _compare_browser_events(
    *,
    workspace: Path,
    applied_at: str,
    site_key: str,
    phase: str,
    recent_limit: int,
) -> dict[str, Any]:
    rows = _read_jsonl(workspace / "evolution" / "browser_control" / "phase_events.jsonl")
    filtered = [
        row
        for row in rows
        if _matches_scope(row, site_key=site_key, phase=phase)
    ]
    before = [row for row in filtered if _is_before(str(row.get("created_at") or ""), applied_at)]
    after = [row for row in filtered if _is_after_or_equal(str(row.get("created_at") or ""), applied_at)]
    return {
        "source": str(workspace / "evolution" / "browser_control" / "phase_events.jsonl"),
        "before": _event_totals(before[-_positive_limit(recent_limit) :]),
        "after": _event_totals(after[-_positive_limit(recent_limit) :]),
    }


def _compare_batches(*, workspace: Path, applied_at: str, site_key: str, recent_limit: int) -> dict[str, Any]:
    batches = JobStore(workspace).list_batches(include_terminal=True)
    filtered = [_site_batch_row(batch, site_key=site_key) for batch in batches]
    rows = [row for row in filtered if row]
    before = [row for row in rows if _is_before(str(row.get("updated_at") or row.get("created_at") or ""), applied_at)]
    after = [row for row in rows if _is_after_or_equal(str(row.get("updated_at") or row.get("created_at") or ""), applied_at)]
    return {
        "source": str(workspace / "jobs" / "batches"),
        "before": _batch_totals(before[-_positive_limit(recent_limit) :]),
        "after": _batch_totals(after[-_positive_limit(recent_limit) :]),
    }


def _select_result(*, metrics: dict[str, Any], browser_events: dict[str, Any], batches: dict[str, Any]) -> dict[str, Any]:
    before_metrics = metrics["before"]
    after_metrics = metrics["after"]
    before_events = browser_events["before"]
    after_events = browser_events["after"]
    before_batches = batches["before"]
    after_batches = batches["after"]

    followup_observations = (
        int(after_metrics.get("calls") or 0)
        + int(after_events.get("events") or 0)
        + int(after_batches.get("site_runs") or 0)
    )
    rollback_reasons: list[str] = []
    if int(after_events.get("negative_events") or 0) >= int(before_events.get("negative_events") or 0) + 2:
        rollback_reasons.append("negative browser-control events increased")
    if int(after_metrics.get("error_calls") or 0) >= int(before_metrics.get("error_calls") or 0) + 2:
        rollback_reasons.append("LLM error calls increased")
    if int(after_batches.get("blocked_or_failed") or 0) >= int(before_batches.get("blocked_or_failed") or 0) + 2:
        rollback_reasons.append("blocked/failed site runs increased")
    if _retrieved_count_regressed(before_batches, after_batches):
        rollback_reasons.append("retrieved job count dropped sharply")

    if rollback_reasons:
        status = "rollback_recommended"
        reason = "; ".join(rollback_reasons)
        confidence = "medium" if followup_observations >= MIN_FOLLOWUP_OBSERVATIONS else "low"
    elif followup_observations < MIN_FOLLOWUP_OBSERVATIONS:
        status = "keep_observing"
        reason = f"only {followup_observations} follow-up observation(s); need at least {MIN_FOLLOWUP_OBSERVATIONS}"
        confidence = "low"
    elif _efficiency_improved(before_metrics, after_metrics) and not rollback_reasons:
        status = "accepted"
        reason = "follow-up evidence shows lower runtime or token cost without detected quality regression"
        confidence = "medium"
    else:
        status = "keep_observing"
        reason = "follow-up evidence is not clearly better or worse"
        confidence = "medium"

    return {
        "status": status,
        "decision_at": now_iso(),
        "reason": reason,
        "confidence": confidence,
        "followup_observations": followup_observations,
    }


def _metric_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        "calls": 0,
        "ok_calls": 0,
        "error_calls": 0,
        "elapsed_ms": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "unknown_token_calls": 0,
        "avg_elapsed_ms": 0,
        "avg_total_tokens": 0,
    }
    for row in rows:
        totals["calls"] += 1
        if str(row.get("status") or "") == "ok":
            totals["ok_calls"] += 1
        else:
            totals["error_calls"] += 1
        totals["elapsed_ms"] += _int(row.get("elapsed_ms"))
        total_tokens = row.get("total_tokens")
        if total_tokens is None:
            totals["unknown_token_calls"] += 1
        else:
            totals["total_tokens"] += _int(total_tokens)
        totals["input_tokens"] += _int(row.get("input_tokens"))
        totals["output_tokens"] += _int(row.get("output_tokens"))
    if totals["calls"]:
        totals["avg_elapsed_ms"] = round(totals["elapsed_ms"] / totals["calls"], 2)
        known_token_calls = max(1, totals["calls"] - totals["unknown_token_calls"])
        totals["avg_total_tokens"] = round(totals["total_tokens"] / known_token_calls, 2)
    return totals


def _event_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    event_types = Counter(str(row.get("event_type") or "unknown") for row in rows)
    guard_names = Counter(str(row.get("guard_name") or "unknown") for row in rows)
    negative_events = sum(count for name, count in event_types.items() if name in NEGATIVE_BROWSER_EVENTS)
    return {
        "events": len(rows),
        "negative_events": negative_events,
        "event_types": dict(sorted(event_types.items())),
        "guard_names": dict(sorted(guard_names.items())),
    }


def _batch_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(str(row.get("status") or "unknown") for row in rows)
    retrieved_counts = [_int(row.get("retrieved_count")) for row in rows if row.get("retrieved_count") is not None]
    blocked_or_failed = sum(count for status, count in status_counts.items() if status in {"blocked", "failed", "partial_completed"})
    return {
        "site_runs": len(rows),
        "status_counts": dict(sorted(status_counts.items())),
        "blocked_or_failed": blocked_or_failed,
        "retrieved_total": sum(retrieved_counts),
        "retrieved_avg": round(sum(retrieved_counts) / len(retrieved_counts), 2) if retrieved_counts else 0,
    }


def _site_batch_row(batch: dict[str, Any], *, site_key: str) -> dict[str, Any]:
    if not site_key:
        return {
            "batch_id": batch.get("batch_id"),
            "created_at": batch.get("created_at"),
            "updated_at": batch.get("updated_at"),
            "status": batch.get("status"),
            "retrieved_count": _batch_retrieved_count(batch),
        }
    sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
    site = sites.get(site_key)
    if not isinstance(site, dict):
        return {}
    return {
        "batch_id": batch.get("batch_id"),
        "created_at": batch.get("created_at"),
        "updated_at": batch.get("updated_at"),
        "status": site.get("status") or batch.get("status"),
        "retrieved_count": _site_retrieved_count(site),
    }


def _batch_retrieved_count(batch: dict[str, Any]) -> int:
    sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
    return sum(_site_retrieved_count(site) for site in sites.values() if isinstance(site, dict))


def _site_retrieved_count(site: dict[str, Any]) -> int:
    retrieve = site.get("retrieve") if isinstance(site.get("retrieve"), dict) else {}
    return _int(retrieve.get("count") or retrieve.get("retrieved") or retrieve.get("retrieved_count"))


def _retrieved_count_regressed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    before_avg = float(before.get("retrieved_avg") or 0)
    after_avg = float(after.get("retrieved_avg") or 0)
    if before_avg < 5 or after_avg <= 0:
        return False
    return after_avg < before_avg * 0.5


def _efficiency_improved(before: dict[str, Any], after: dict[str, Any]) -> bool:
    before_calls = int(before.get("calls") or 0)
    after_calls = int(after.get("calls") or 0)
    if before_calls <= 0 or after_calls <= 0:
        return False
    before_elapsed = float(before.get("avg_elapsed_ms") or 0)
    after_elapsed = float(after.get("avg_elapsed_ms") or 0)
    before_tokens = float(before.get("avg_total_tokens") or 0)
    after_tokens = float(after.get("avg_total_tokens") or 0)
    elapsed_improved = before_elapsed > 0 and after_elapsed <= before_elapsed * 0.9
    token_improved = before_tokens > 0 and after_tokens <= before_tokens * 0.9
    return elapsed_improved or token_improved


def _matches_scope(row: dict[str, Any], *, site_key: str, phase: str) -> bool:
    if site_key and str(row.get("site_key") or row.get("site_id") or "") != site_key:
        return False
    if phase and str(row.get("phase") or "") != phase:
        return False
    return True


def _infer_primary_phase(run_payload: dict[str, Any]) -> str:
    candidate_id = str(run_payload.get("candidate_id") or "")
    if candidate_id == "site_workflow_compaction":
        return "job_retrieval"
    return ""


def _infer_site_key(*, run_payload: dict[str, Any], applied_files: list[Any]) -> str:
    for row in applied_files:
        if not isinstance(row, dict):
            continue
        site_key = _site_from_path(str(row.get("relative_path") or row.get("target_file") or ""))
        if site_key:
            return site_key
    candidate = run_payload.get("candidate") if isinstance(run_payload.get("candidate"), dict) else {}
    return _site_from_path(str(candidate.get("target_ref") or ""))


def _site_from_path(value: str) -> str:
    parts = Path(value).parts
    for idx, part in enumerate(parts[:-1]):
        if part == "sites" and idx + 1 < len(parts):
            candidate = parts[idx + 1]
            if candidate and "<" not in candidate and ">" not in candidate:
                return candidate
    return ""


def _resolve_run_path(run_dir: Path, value: Any) -> Path:
    text = str(value or "").strip()
    if not text:
        return Path()
    path = Path(text)
    if path.is_absolute():
        return path
    return run_dir / path


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return JSONLStore(path).read_all()


def _positive_limit(value: int) -> int:
    return max(1, int(value or DEFAULT_RECENT_LIMIT))


def _is_before(ts: str, boundary: str) -> bool:
    if not boundary:
        return True
    left = _parse_ts(ts)
    right = _parse_ts(boundary)
    if left is None or right is None:
        return ts < boundary
    try:
        return left < right
    except TypeError:
        return ts < boundary


def _is_after_or_equal(ts: str, boundary: str) -> bool:
    if not boundary:
        return True
    left = _parse_ts(ts)
    right = _parse_ts(boundary)
    if left is None or right is None:
        return ts >= boundary
    try:
        return left >= right
    except TypeError:
        return ts >= boundary


def _parse_ts(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _render_evaluation_markdown(payload: dict[str, Any]) -> str:
    selection = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
    scope = payload.get("scope") if isinstance(payload.get("scope"), dict) else {}
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    events = payload.get("browser_events") if isinstance(payload.get("browser_events"), dict) else {}
    batches = payload.get("batches") if isinstance(payload.get("batches"), dict) else {}
    return (
        "# Evolution Evaluation\n\n"
        f"- Run ID: `{payload.get('run_id')}`\n"
        f"- Candidate: `{payload.get('candidate_id')}`\n"
        f"- Evaluated At: {payload.get('evaluated_at')}\n"
        f"- Site: `{scope.get('site_key') or 'all'}`\n"
        f"- Phase: `{scope.get('phase') or 'all'}`\n"
        f"- Selection: `{selection.get('status')}`\n"
        f"- Reason: {selection.get('reason')}\n"
        f"- Confidence: `{selection.get('confidence')}`\n\n"
        "## Metrics\n\n"
        f"- Before: `{metrics.get('before')}`\n"
        f"- After: `{metrics.get('after')}`\n\n"
        "## Browser Events\n\n"
        f"- Before: `{events.get('before')}`\n"
        f"- After: `{events.get('after')}`\n\n"
        "## Batch Outcomes\n\n"
        f"- Before: `{batches.get('before')}`\n"
        f"- After: `{batches.get('after')}`\n"
    )


def _render_review_only_evaluation_markdown(payload: dict[str, Any]) -> str:
    selection = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
    review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    action_card = str(review.get("action_card") or "").strip()
    action_card_line = f"- Action Card: `{action_card}`\n" if action_card else ""
    return (
        "# Evolution Evaluation\n\n"
        f"- Run ID: `{payload.get('run_id')}`\n"
        f"- Candidate: `{payload.get('candidate_id')}`\n"
        f"- Evaluated At: {payload.get('evaluated_at')}\n"
        f"- Selection: `{selection.get('status')}`\n"
        f"- Reason: {selection.get('reason')}\n"
        f"- Codex Review Required: `{str(review.get('codex_review_required')).lower()}`\n"
        f"- Codex Review Pack: `{review.get('review_pack')}`\n\n"
        f"{action_card_line}"
        "## Metrics\n\n"
        f"- Intake Events: `{metrics.get('intake_count')}`\n"
        f"- Explicit Intake Events: `{metrics.get('explicit_intake_count')}`\n"
        f"- Memory Units: `{metrics.get('memory_unit_count')}`\n"
        f"- Corrections: `{metrics.get('correction_count')}`\n"
        f"- Codex Imported Memory Units: `{metrics.get('codex_imported_memory_count')}`\n"
        f"- Possible Duplicate Groups: `{metrics.get('duplicate_group_count')}`\n\n"
        "## Next Expected Stage\n\n"
        "Ask Codex to review the generated review pack and recommend accepted, keep_observing, low_confidence, rejected, or rollback_recommended.\n"
    )


def _update_summary(*, run_dir: Path, run_payload: dict[str, Any], evaluation: dict[str, Any]) -> None:
    candidate = run_payload.get("candidate") if isinstance(run_payload.get("candidate"), dict) else {}
    selection = evaluation.get("selection") if isinstance(evaluation.get("selection"), dict) else {}
    lines = [
        "# Evolution Run Summary",
        "",
        f"- Run ID: `{run_payload.get('run_id')}`",
        f"- Status: `{run_payload.get('status')}`",
        f"- Candidate: `{run_payload.get('candidate_id')}`",
        f"- Target: `{candidate.get('target_ref') or ''}`",
        f"- Risk: `{candidate.get('risk_level') or ''}`",
        f"- Apply Policy: `{candidate.get('apply_policy') or ''}`",
        "",
        "## Evaluation",
        "",
        f"- Selection: `{selection.get('status')}`",
        f"- Reason: {selection.get('reason')}",
        f"- Confidence: `{selection.get('confidence')}`",
        "",
        "## Next Expected Stage",
        "",
        "If selection is accepted, retain the proposal. If selection is rollback_recommended, run rollback or inspect manually. If selection is keep_observing, collect more follow-up runs.",
    ]
    (run_dir / "summary.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
