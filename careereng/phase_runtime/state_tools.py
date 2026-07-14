"""Shared CareerEng state tools for provider and external-agent phase drivers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from careereng.phase_runtime.contracts import (
    PHASE_RESULT_TOOL,
    RECORD_APPLICATION_REVIEWS_TOOL,
    RECORD_JOBS_TOOL,
    REQUEST_CONTEXT_TOOL,
    UPDATE_JOBS_TOOL,
    UPDATE_PHASE_MEMORY_TOOL,
    normalize_tool_name,
)
from careereng.phase_runtime.results import phase_result_payload


@dataclass
class PhaseStateToolContext:
    site_store: Any
    site_key: str
    session_id: str = ""
    turn_id: str = ""
    batch_id: str = ""
    current_url: str = ""
    context_session: Any | None = None
    phase_memory: Any | None = None
    retrieval_history_stop_success_ratio: float = 0.4
    retrieval_history_stop_min_page_jobs: int = 10


def phase_result_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "name": PHASE_RESULT_TOOL,
        "description": "Report that the current phase is done or blocked.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["done", "blocked"]},
                "summary": {"type": "string"},
            },
            "required": ["status", "summary"],
            "additionalProperties": False,
        },
    }


def record_jobs_tool_schema() -> dict[str, Any]:
    job_properties = {
        "title": {"type": "string"},
        "url": {"type": "string"},
        "location": {"type": "string"},
        "posted_label": {"type": "string"},
        "employment_type": {"type": "string"},
        "match_label": {"type": "string"},
        "apply_state": {"type": "string"},
        "site_job_id": {"type": "string"},
        "posted_at": {"type": "string"},
    }
    return {
        "type": "function",
        "name": RECORD_JOBS_TOOL,
        "description": "Persist the full visible job list from the current page for later retrieval and apply phases.",
        "strict": False,
        "parameters": {
            "type": "object",
            "properties": {
                "jobs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": job_properties,
                        "required": ["title", "url"],
                        "additionalProperties": False,
                    },
                },
                "newest_first_confirmed": {
                    "type": "boolean",
                    "description": "True only when the current visible results order is confirmed newest/date-posted first by visible sort state, URL state, or stable phase memory.",
                },
            },
            "required": ["jobs"],
            "additionalProperties": False,
        },
    }


def update_jobs_tool_schema() -> dict[str, Any]:
    job_properties = {
        "job_id": {"type": "string"},
        "title": {"type": "string"},
        "url": {"type": "string"},
        "location": {"type": "string"},
        "posted_label": {"type": "string"},
        "employment_type": {"type": "string"},
        "match_label": {"type": "string"},
        "apply_state": {"type": "string"},
        "site_job_id": {"type": "string"},
        "posted_at": {"type": "string"},
        "description": {"type": "string"},
        "jd_sync_status": {"type": "string"},
        "decision_status": {"type": "string"},
        "decision_rule_source": {"type": "string"},
        "decision_rule_name": {"type": "string"},
        "site_match_signal_raw": {"type": "string"},
        "match_score_initial": {"type": "number"},
        "match_reason_initial": {"type": "string"},
        "match_score_final": {"type": "number"},
        "match_reason_final": {"type": "string"},
        "fit_apply": {"type": "boolean"},
        "fit_confidence": {"type": "number"},
        "fit_reason": {"type": "string"},
        "fit_source": {"type": "string"},
        "application_status": {"type": "string"},
        "application_status_raw": {"type": "string"},
        "decision_reason_type": {"type": "string"},
        "decision_context_hash": {"type": "string"},
        "last_apply_error": {"type": "string"},
        "block_reason_type": {"type": "string"},
        "failure_pattern": {"type": "string"},
        "loop_control_action": {"type": "string"},
        "recommended_target": {"type": "string"},
        "loop_scope": {"type": "string"},
        "gap_type": {"type": "string"},
        "recommended_action": {"type": "string"},
        "target": {"type": "string"},
        "resume_policy": {"type": "string"},
        "current_item_ref": {"type": "string"},
        "evidence": {"type": "string"},
        "refinement_hint": {"type": "string"},
    }
    return {
        "type": "function",
        "name": UPDATE_JOBS_TOOL,
        "description": "Persist current per-job JD, decision, and application state for the active batch run.",
        "strict": False,
        "parameters": {
            "type": "object",
            "properties": {
                "jobs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": job_properties,
                        "required": ["job_id"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["jobs"],
            "additionalProperties": False,
        },
    }


def record_application_reviews_tool_schema() -> dict[str, Any]:
    review_properties = {
        "title": {"type": "string"},
        "url": {"type": "string"},
        "site_job_id": {"type": "string"},
        "application_review_status": {
            "type": "string",
            "enum": ["active", "inactive", "resumable", "rejected", "closed", "withdrawn", "unknown", "blocked"],
        },
        "application_review_status_raw": {"type": "string"},
        "application_review_stage": {"type": "string"},
    }
    return {
        "type": "function",
        "name": RECORD_APPLICATION_REVIEWS_TOOL,
        "description": "Persist website-visible submitted-application review statuses for the active site.",
        "strict": False,
        "parameters": {
            "type": "object",
            "properties": {
                "reviews": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": review_properties,
                        "required": ["title", "application_review_status"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["reviews"],
            "additionalProperties": False,
        },
    }


def request_context_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "name": REQUEST_CONTEXT_TOOL,
        "description": (
            "Request an additional preloaded context bundle for the current apply phase when the live page, "
            "site skill, and lightweight facts are insufficient. This does not operate the browser."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "bundle": {"type": "string", "enum": ["apply_facts", "full_cv", "full_persona"]},
                "reason": {"type": "string"},
            },
            "required": ["bundle", "reason"],
            "additionalProperties": False,
        },
    }


def update_phase_memory_tool_schema() -> dict[str, Any]:
    entry_schema = {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "text": {"type": "string"},
        },
        "required": ["key", "text"],
        "additionalProperties": False,
    }
    return {
        "type": "function",
        "name": UPDATE_PHASE_MEMORY_TOOL,
        "description": (
            "Record phase-local completed, confirmed, pending, and do-not-repeat facts for the current phase. "
            "This does not operate the browser."
        ),
        "strict": False,
        "parameters": {
            "type": "object",
            "properties": {
                "completed": {"type": "array", "items": entry_schema},
                "confirmed": {"type": "array", "items": entry_schema},
                "pending": {"type": "array", "items": entry_schema},
                "do_not_repeat": {"type": "array", "items": entry_schema},
                "metrics": {
                    "type": "object",
                    "properties": {
                        "results_count": {"type": "integer"},
                        "total_pages": {"type": "integer"},
                        "page_size": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
                "clear_keys": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    }


def state_tool_schema(tool_name: str) -> dict[str, Any]:
    normalized = normalize_tool_name(tool_name)
    if normalized == PHASE_RESULT_TOOL:
        return phase_result_tool_schema()
    if normalized == RECORD_JOBS_TOOL:
        return record_jobs_tool_schema()
    if normalized == UPDATE_JOBS_TOOL:
        return update_jobs_tool_schema()
    if normalized == RECORD_APPLICATION_REVIEWS_TOOL:
        return record_application_reviews_tool_schema()
    if normalized == REQUEST_CONTEXT_TOOL:
        return request_context_tool_schema()
    if normalized == UPDATE_PHASE_MEMORY_TOOL:
        return update_phase_memory_tool_schema()
    raise ValueError(f"unknown state tool: {tool_name}")


def state_tool_schemas_for_phase(phase_slug: str, *, include_phase_result: bool = False) -> list[dict[str, Any]]:
    phase = str(phase_slug or "").strip()
    tools = [update_phase_memory_tool_schema()]
    if phase == "application_status_review":
        tools.append(record_application_reviews_tool_schema())
    if phase == "job_retrieval":
        tools.append(record_jobs_tool_schema())
    if phase == "apply":
        tools.append(update_jobs_tool_schema())
        tools.append(request_context_tool_schema())
    if include_phase_result:
        tools.append(phase_result_tool_schema())
    return tools


def execute_state_tool(tool_name: str, arguments: dict[str, Any] | None, context: PhaseStateToolContext) -> dict[str, Any]:
    normalized = normalize_tool_name(tool_name)
    args = arguments if isinstance(arguments, dict) else {}
    if normalized == PHASE_RESULT_TOOL:
        return phase_result_payload(args)
    if normalized == RECORD_JOBS_TOOL:
        return _record_jobs_payload(context=context, arguments=args)
    if normalized == UPDATE_JOBS_TOOL:
        return _update_jobs_payload(context=context, arguments=args)
    if normalized == RECORD_APPLICATION_REVIEWS_TOOL:
        return _record_application_reviews_payload(context=context, arguments=args)
    if normalized == REQUEST_CONTEXT_TOOL:
        return _request_context_payload(context_session=context.context_session, arguments=args)
    if normalized == UPDATE_PHASE_MEMORY_TOOL:
        return _update_phase_memory_payload(phase_memory=context.phase_memory, arguments=args)
    return {
        "isError": True,
        "error": f"unknown state tool: {tool_name}",
        "structuredContent": {"tool_name": normalized},
        "content": [{"type": "text", "text": f"Unknown state tool: {tool_name}"}],
    }


def _normalize_record_job(job: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = (
        "title",
        "url",
        "location",
        "posted_label",
        "employment_type",
        "match_label",
        "apply_state",
        "site_job_id",
        "posted_at",
    )
    normalized: dict[str, Any] = {}
    for key in allowed_keys:
        value = job.get(key)
        if value is None:
            continue
        normalized[key] = value.strip() if isinstance(value, str) else str(value).strip()
    return normalized


def _is_apply_terminal_job_state(row: dict[str, Any] | None) -> bool:
    if not isinstance(row, dict):
        return False
    decision_status = str(row.get("decision_status") or "").strip().lower()
    application_status = str(row.get("application_status") or "").strip().lower()
    apply_state = str(row.get("apply_state") or "").strip().lower()
    terminal_apply_states = {
        "submitted",
        "already_applied",
        "filtered_out",
        "blocked",
        "apply_failed",
        "terminal_submitted",
        "terminal_application_received",
        "terminal_already_applied",
        "terminal_filtered_out",
        "terminal_blocked",
        "terminal_apply_failed",
        "terminal_rejected",
        "terminal_closed",
        "terminal_withdrawn",
    }
    return (
        decision_status in {"filtered_out", "already_applied"}
        or application_status
        in {"already_applied", "filtered_out", "submitted", "apply_failed", "blocked", "rejected", "closed", "withdrawn"}
        or apply_state in terminal_apply_states
    )


def _is_history_operation_success(row: dict[str, Any] | None) -> bool:
    if not isinstance(row, dict):
        return False
    decision_status = str(row.get("decision_status") or "").strip().lower()
    application_status = str(row.get("application_status") or "").strip().lower()
    application_review_status = str(row.get("application_review_status") or "").strip().lower()
    apply_state = str(row.get("apply_state") or "").strip().lower()
    successful_decisions = {"filtered_out", "skipped_as_not_fit", "not_fit", "already_applied", "submitted"}
    successful_application_statuses = {
        "filtered_out",
        "skipped_as_not_fit",
        "not_fit",
        "active",
        "in_process",
        "in_review",
        "resume_review",
        "assessment",
        "interview",
        "offer",
        "submitted",
        "already_applied",
        "application_received",
        "received",
        "rejected",
        "closed",
        "withdrawn",
    }
    successful_review_statuses = {
        "active",
        "in_process",
        "in_review",
        "resume_review",
        "assessment",
        "interview",
        "offer",
        "submitted",
        "application_received",
        "received",
        "rejected",
        "closed",
        "withdrawn",
    }
    successful_apply_states = {
        "filtered_out",
        "terminal_filtered_out",
        "terminal_submitted",
        "terminal_already_applied",
        "terminal_application_received",
        "terminal_rejected",
        "terminal_closed",
        "terminal_withdrawn",
    }
    return (
        decision_status in successful_decisions
        or application_status in successful_application_statuses
        or application_review_status in successful_review_statuses
        or apply_state in successful_apply_states
    )


def _record_jobs_payload(*, context: PhaseStateToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    raw_jobs = arguments.get("jobs")
    if not isinstance(raw_jobs, list):
        raw_jobs = []
    jobs = [_normalize_record_job(job) for job in raw_jobs if isinstance(job, dict)]
    jobs = [job for job in jobs if job]
    site_store = context.site_store
    classify_history_matches = getattr(site_store, "classify_history_matches", None)
    list_jobs = getattr(site_store, "list_jobs", None)
    preview_new_flags = getattr(site_store, "preview_history_new_flags", None)
    before_rows = list_jobs(context.site_key) if callable(list_jobs) else []
    before_ids = {str(row.get("job_id") or "") for row in before_rows if isinstance(row, dict)}
    if callable(classify_history_matches):
        try:
            history_matches = list(classify_history_matches(context.site_key, jobs))
        except Exception:
            history_matches = []
    else:
        history_matches = []
    if not history_matches and callable(preview_new_flags):
        try:
            new_flags = list(preview_new_flags(context.site_key, jobs))
        except Exception:
            new_flags = []
    else:
        new_flags = [row.get("history_match_status") == "new" for row in history_matches]
    newest_first_confirmed = bool(arguments.get("newest_first_confirmed"))
    if newest_first_confirmed:
        saved_rows = site_store.append_jobs(
            context.site_key,
            jobs,
            context.session_id or "",
            context.turn_id,
            context.batch_id,
            newest_first_confirmed=True,
        )
    else:
        saved_rows = site_store.append_jobs(context.site_key, jobs, context.session_id or "", context.turn_id, context.batch_id)
    saved_ids: list[str] = []
    new_ids: list[str] = []
    history_match_results: list[dict[str, Any]] = []
    for idx, row in enumerate(saved_rows):
        if not isinstance(row, dict):
            continue
        record_id = str(row.get("observation_id") or row.get("job_id") or "").strip()
        if not record_id:
            continue
        run_job_id = str(row.get("job_id") or "").strip()
        saved_ids.append(record_id)
        is_new = idx < len(new_flags) and bool(new_flags[idx])
        if not new_flags and str(row.get("job_id") or "").strip() not in before_ids:
            is_new = True
        if is_new:
            new_ids.append(record_id)
        classification = history_matches[idx] if idx < len(history_matches) and isinstance(history_matches[idx], dict) else {}
        status = str(classification.get("history_match_status") or ("new" if is_new else "existing_complete"))
        reasons = classification.get("enrichment_reasons")
        if not isinstance(reasons, list):
            reasons = []
        history_match_results.append(
            {
                "record_id": record_id,
                "job_id": run_job_id,
                "title": str(row.get("title") or ""),
                "url": str(row.get("url") or ""),
                "site_job_id": str(row.get("site_job_id") or ""),
                "history_match_status": status,
                "matched_job_id": str(classification.get("matched_job_id") or ""),
                "decision_status": str(classification.get("decision_status") or ""),
                "apply_state": str(classification.get("apply_state") or ""),
                "application_status": str(classification.get("application_status") or ""),
                "application_review_status": str(classification.get("application_review_status") or ""),
                "application_review_status_raw": str(classification.get("application_review_status_raw") or ""),
                "enrichment_reasons": [str(reason) for reason in reasons if str(reason).strip()],
            }
        )
        history_match_results[-1]["operation_success"] = _is_history_operation_success(history_match_results[-1])
    recorded_count = len(saved_ids)
    new_count = len(new_ids)
    existing_count = sum(1 for item in history_match_results if str(item.get("history_match_status") or "").startswith("existing_"))
    enrichment_needed = [item for item in history_match_results if item.get("history_match_status") == "existing_needs_enrichment"]
    existing_complete_count = sum(1 for item in history_match_results if item.get("history_match_status") == "existing_complete")
    enrichment_needed_count = len(enrichment_needed)
    operation_success_count = sum(1 for item in history_match_results if bool(item.get("operation_success")))
    operation_success_ratio = operation_success_count / recorded_count if recorded_count else 0.0
    stop_success_ratio_threshold = max(0.0, float(context.retrieval_history_stop_success_ratio or 0.0))
    stop_min_page_jobs = max(1, int(context.retrieval_history_stop_min_page_jobs or 1))
    stop_recommended = bool(recorded_count >= stop_min_page_jobs and operation_success_ratio >= stop_success_ratio_threshold)
    stop_reason = "current page reached the operation-success ratio threshold" if stop_recommended else ""
    summary = (
        f"Recorded {recorded_count} jobs from the current page "
        f"({new_count} new, {existing_count} existing, {enrichment_needed_count} need enrichment, "
        f"{operation_success_count} operation-success, success_ratio={operation_success_ratio:.2f})."
    )
    if stop_recommended:
        summary += (
            " Stop pagination is recommended by operation-success history policy "
            f"(threshold={stop_success_ratio_threshold:.2f}, min_page_jobs={stop_min_page_jobs})."
        )
    return {
        "isError": False,
        "current_url": context.current_url,
        "structuredContent": {
            "current_url": context.current_url,
            "recorded_count": recorded_count,
            "new_count": new_count,
            "existing_count": existing_count,
            "existing_complete_count": existing_complete_count,
            "enrichment_needed_count": enrichment_needed_count,
            "operation_success_count": operation_success_count,
            "operation_success_ratio": operation_success_ratio,
            "history_stop_success_ratio_threshold": stop_success_ratio_threshold,
            "history_stop_min_page_jobs": stop_min_page_jobs,
            "newest_first_confirmed": newest_first_confirmed,
            "stop_recommended": stop_recommended,
            "stop_reason": stop_reason,
            "job_ids": saved_ids,
            "new_job_ids": new_ids,
            "history_matches": history_match_results,
            "enrichment_job_ids": [
                str(item.get("job_id") or item.get("record_id") or "")
                for item in enrichment_needed
                if str(item.get("job_id") or item.get("record_id") or "")
            ],
        },
        "content": [{"type": "text", "text": summary}],
    }


def _update_jobs_payload(*, context: PhaseStateToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    raw_jobs = arguments.get("jobs")
    jobs = [dict(job) for job in raw_jobs if isinstance(job, dict)] if isinstance(raw_jobs, list) else []
    saved_rows = context.site_store.update_run_jobs(
        context.site_key,
        jobs,
        context.session_id or "",
        context.turn_id,
        context.batch_id,
    )
    updated_ids = [str(row.get("job_id") or "").strip() for row in saved_rows if str(row.get("job_id") or "").strip()]
    terminal_ids = [
        str(row.get("job_id") or "").strip()
        for row in saved_rows
        if _is_apply_terminal_job_state(row) and str(row.get("job_id") or "").strip()
    ]
    return {
        "isError": False,
        "structuredContent": {
            "updated_count": len(updated_ids),
            "job_ids": updated_ids,
            "terminal_count": len(terminal_ids),
            "terminal_job_ids": terminal_ids,
        },
        "content": [{"type": "text", "text": f"Updated {len(updated_ids)} jobs in the current batch run."}],
    }


def _record_application_reviews_payload(*, context: PhaseStateToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    raw_reviews = arguments.get("reviews")
    reviews = [dict(row) for row in raw_reviews if isinstance(row, dict)] if isinstance(raw_reviews, list) else []
    append_reviews = getattr(context.site_store, "append_application_reviews", None)
    if not callable(append_reviews):
        raise RuntimeError("site store does not support application reviews")
    summary = append_reviews(context.site_key, reviews, context.session_id or "", context.turn_id, context.batch_id)
    recorded_count = int(summary.get("recorded_count") or 0) if isinstance(summary, dict) else 0
    matched_count = int(summary.get("matched_count") or 0) if isinstance(summary, dict) else 0
    unmatched_count = int(summary.get("unmatched_count") or 0) if isinstance(summary, dict) else 0
    created_history_count = int(summary.get("created_history_count") or 0) if isinstance(summary, dict) else 0
    matched_job_ids = summary.get("matched_job_ids") if isinstance(summary, dict) else []
    if not isinstance(matched_job_ids, list):
        matched_job_ids = []
    text = f"Recorded {recorded_count} application reviews ({matched_count} matched history, {unmatched_count} unmatched)."
    if created_history_count:
        text += f" Created {created_history_count} minimal history row(s)."
    return {
        "isError": False,
        "structuredContent": {
            "recorded_count": recorded_count,
            "matched_count": matched_count,
            "unmatched_count": unmatched_count,
            "created_history_count": created_history_count,
            "matched_job_ids": [str(job_id) for job_id in matched_job_ids if str(job_id).strip()],
        },
        "content": [{"type": "text", "text": text}],
    }


def _request_context_payload(*, context_session: Any | None, arguments: dict[str, Any]) -> dict[str, Any]:
    if context_session is None:
        return {
            "isError": False,
            "structuredContent": {
                "bundle": str(arguments.get("bundle") or ""),
                "available": False,
                "status": "context_session_unavailable",
            },
            "content": [{"type": "text", "text": "### Result\n- No browser context session is available for this phase."}],
        }
    request_bundle = getattr(context_session, "request_bundle", None)
    if not callable(request_bundle):
        return {
            "isError": False,
            "structuredContent": {
                "bundle": str(arguments.get("bundle") or ""),
                "available": False,
                "status": "context_session_invalid",
            },
            "content": [{"type": "text", "text": "### Result\n- The current context session cannot serve bundles."}],
        }
    return dict(request_bundle(bundle=str(arguments.get("bundle") or ""), reason=str(arguments.get("reason") or "")))


def _phase_memory_entries(raw_entries: Any) -> list[tuple[str, str]]:
    if not isinstance(raw_entries, list):
        return []
    entries: list[tuple[str, str]] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        text = str(item.get("text") or "").strip()
        if key and text:
            entries.append((key, text))
    return entries


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _update_phase_memory_payload(*, phase_memory: Any | None, arguments: dict[str, Any]) -> dict[str, Any]:
    if phase_memory is None:
        return {
            "isError": False,
            "structuredContent": {
                "status": "phase_memory_unavailable",
                "completed_count": 0,
                "confirmed_count": 0,
                "pending_count": 0,
                "do_not_repeat_count": 0,
                "metrics_count": 0,
                "cleared_count": 0,
            },
            "content": [{"type": "text", "text": "### Result\n- No phase memory is available for this phase."}],
        }

    clear_keys = [
        str(item or "").strip()
        for item in (arguments.get("clear_keys") if isinstance(arguments.get("clear_keys"), list) else [])
        if str(item or "").strip()
    ]
    for key in clear_keys:
        phase_memory.drop(key)

    counts = {"completed": 0, "confirmed": 0, "pending": 0, "do_not_repeat": 0}
    for key, text in _phase_memory_entries(arguments.get("completed")):
        phase_memory.set_completed(key=key, text=text)
        counts["completed"] += 1
    for key, text in _phase_memory_entries(arguments.get("confirmed")):
        phase_memory.set_confirmed(key=key, text=text)
        counts["confirmed"] += 1
    for key, text in _phase_memory_entries(arguments.get("pending")):
        phase_memory.set_pending(key=key, text=text)
        counts["pending"] += 1
    for key, text in _phase_memory_entries(arguments.get("do_not_repeat")):
        phase_memory.set_do_not_repeat(key=key, text=text)
        counts["do_not_repeat"] += 1

    metrics_count = 0
    raw_metrics = arguments.get("metrics")
    if isinstance(raw_metrics, dict):
        for key in ("results_count", "total_pages", "page_size"):
            value = _positive_int(raw_metrics.get(key))
            if value is None:
                continue
            phase_memory.set_metric(key=key, value=value)
            metrics_count += 1

    total_changes = sum(counts.values()) + metrics_count + len(clear_keys)
    if total_changes <= 0:
        return {
            "isError": False,
            "structuredContent": {
                "status": "no_change",
                "completed_count": 0,
                "confirmed_count": 0,
                "pending_count": 0,
                "do_not_repeat_count": 0,
                "metrics_count": 0,
                "cleared_count": 0,
                "clear_keys": [],
            },
            "content": [{"type": "text", "text": "### Result\n- Phase memory unchanged."}],
        }

    lines = ["### Result", "- Updated current phase memory."]
    if clear_keys:
        lines.append(f"- Cleared keys: {', '.join(clear_keys)}")
    if counts["completed"] > 0:
        lines.append(f"- Added completed items: {counts['completed']}")
    if counts["confirmed"] > 0:
        lines.append(f"- Added confirmed items: {counts['confirmed']}")
    if counts["pending"] > 0:
        lines.append(f"- Added pending items: {counts['pending']}")
    if counts["do_not_repeat"] > 0:
        lines.append(f"- Added do-not-repeat items: {counts['do_not_repeat']}")
    if metrics_count > 0:
        lines.append(f"- Added metrics: {metrics_count}")
    return {
        "isError": False,
        "structuredContent": {
            "status": "updated",
            "completed_count": counts["completed"],
            "confirmed_count": counts["confirmed"],
            "pending_count": counts["pending"],
            "do_not_repeat_count": counts["do_not_repeat"],
            "metrics_count": metrics_count,
            "cleared_count": len(clear_keys),
            "clear_keys": clear_keys,
        },
        "content": [{"type": "text", "text": "\n".join(lines)}],
    }
