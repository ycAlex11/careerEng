"""State-tool command handlers for orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from careereng.platform.cache import CacheArtifactError, CacheArtifactStore
from careereng.career.applications.ranked_queue import (
    DEFERRED_BY_RANK,
    RANKING_PENDING,
    is_ranked_review_complete,
    validate_ranking_pending_update,
)

from careereng.orchestration.agent_protocol.state_tools import (
    PHASE_RESULT_TOOL,
    RECORD_APPLICATION_REVIEWS_TOOL,
    RECORD_JOBS_TOOL,
    REQUEST_CONTEXT_TOOL,
    UPDATE_JOBS_TOOL,
    UPDATE_PHASE_MEMORY_TOOL,
    CACHE_LOOKUP_TOOL,
    CACHE_READ_TOOL,
    CACHE_PROPOSE_TOOL,
    CACHE_VALIDATE_TOOL,
    RECORD_EVOLUTION_SIGNAL_TOOL,
    normalize_tool_name,
    phase_result_tool_schema,
    record_application_reviews_tool_schema,
    record_jobs_tool_schema,
    request_context_tool_schema,
    state_tool_schema,
    state_tool_schemas_for_phase,
    update_jobs_tool_schema,
    update_phase_memory_tool_schema,
)
from careereng.orchestration.agent_protocol.results import phase_result_payload
from careereng.orchestration.engine.phase_orchestration import (
    phase_completion_gate,
    record_retrieval_history_evidence,
)


@dataclass
class PhaseStateToolContext:
    site_store: Any
    site_key: str
    session_id: str = ""
    turn_id: str = ""
    batch_id: str = ""
    current_url: str = ""
    phase_slug: str = ""
    context_session: Any | None = None
    phase_memory: Any | None = None
    persist_phase_memory: Callable[[Any], None] | None = None
    retrieval_history_stop_success_ratio: float = 0.4
    retrieval_history_stop_min_page_jobs: int = 10
    workspace: Path | str | None = None
    cache_scope: dict[str, Any] | None = None
    cache_dependency_versions: dict[str, Any] | None = None
    record_evolution_signal: Callable[[dict[str, Any]], dict[str, Any]] | None = None



def execute_state_tool(tool_name: str, arguments: dict[str, Any] | None, context: PhaseStateToolContext) -> dict[str, Any]:
    normalized = normalize_tool_name(tool_name)
    args = arguments if isinstance(arguments, dict) else {}
    if normalized == PHASE_RESULT_TOOL:
        payload = phase_result_payload(args)
        structured = payload.get("structuredContent") if isinstance(payload.get("structuredContent"), dict) else {}
        gate = phase_completion_gate(
            phase_slug=str(getattr(context, "phase_slug", "") or ""),
            result_status=str(structured.get("status") or ""),
            phase_memory=getattr(context, "phase_memory", None),
        )
        if gate.allowed:
            return payload
        return {
            "isError": True,
            "error": "retrieval_confirmation_required",
            "structuredContent": {"status": "continue_current", "reason": "retrieval_confirmation_required"},
            "content": [{"type": "text", "text": gate.message}],
        }
    if normalized == RECORD_JOBS_TOOL:
        payload = _record_jobs_payload(context=context, arguments=args)
        structured = payload.get("structuredContent") if isinstance(payload.get("structuredContent"), dict) else {}
        transition = record_retrieval_history_evidence(context.phase_memory, structured)
        if isinstance(structured, dict):
            structured["retrieval_orchestration"] = transition.as_dict()
        if callable(context.persist_phase_memory) and context.phase_memory is not None:
            try:
                context.persist_phase_memory(context.phase_memory)
            except Exception as exc:
                return {
                    "isError": True,
                    "error": f"phase memory persistence failed: {exc}",
                    "structuredContent": {"status": "persistence_failed"},
                    "content": [{"type": "text", "text": "### Result\n- Retrieval progress could not be persisted."}],
                }
        if transition.message:
            payload["content"] = [
                *[item for item in payload.get("content") or [] if isinstance(item, dict)],
                {"type": "text", "text": transition.message},
            ]
        return payload
    if normalized == UPDATE_JOBS_TOOL:
        return _update_jobs_payload(context=context, arguments=args)
    if normalized == RECORD_APPLICATION_REVIEWS_TOOL:
        return _record_application_reviews_payload(context=context, arguments=args)
    if normalized == REQUEST_CONTEXT_TOOL:
        return _request_context_payload(context_session=context.context_session, arguments=args)
    if normalized == UPDATE_PHASE_MEMORY_TOOL:
        payload = _update_phase_memory_payload(phase_memory=context.phase_memory, arguments=args)
        structured = payload.get("structuredContent") if isinstance(payload.get("structuredContent"), dict) else {}
        if (
            not payload.get("isError")
            and str(structured.get("status") or "") == "updated"
            and callable(context.persist_phase_memory)
        ):
            try:
                context.persist_phase_memory(context.phase_memory)
            except Exception as exc:
                return {
                    "isError": True,
                    "error": f"phase memory persistence failed: {exc}",
                    "structuredContent": {"status": "persistence_failed"},
                    "content": [{"type": "text", "text": "### Result\n- Phase memory update could not be persisted."}],
                }
        return payload
    if normalized == RECORD_EVOLUTION_SIGNAL_TOOL:
        if not callable(context.record_evolution_signal):
            return {
                "isError": True,
                "error": "evolution signal recording is unavailable for this runtime",
                "structuredContent": {"status": "unavailable"},
                "content": [{"type": "text", "text": "Evolution signal recording is unavailable for this runtime."}],
            }
        try:
            result = context.record_evolution_signal(args)
        except Exception as exc:
            return {
                "isError": True,
                "error": str(exc),
                "structuredContent": {"status": "persistence_failed"},
                "content": [{"type": "text", "text": f"Evolution signal could not be recorded: {exc}"}],
            }
        return {
            "isError": False,
            "structuredContent": {"status": "recorded", **(result if isinstance(result, dict) else {})},
            "content": [{"type": "text", "text": "Evolution signal recorded for the current loop scope."}],
        }
    if normalized == CACHE_LOOKUP_TOOL:
        return _cache_lookup_payload(context=context, arguments=args)
    if normalized == CACHE_READ_TOOL:
        return _cache_read_payload(context=context, arguments=args)
    if normalized == CACHE_PROPOSE_TOOL:
        return _cache_propose_payload(context=context, arguments=args)
    if normalized == CACHE_VALIDATE_TOOL:
        return _cache_validate_payload(context=context, arguments=args)
    return {
        "isError": True,
        "error": f"unknown state tool: {tool_name}",
        "structuredContent": {"tool_name": normalized},
        "content": [{"type": "text", "text": f"Unknown state tool: {tool_name}"}],
    }


def _cache_store(context: PhaseStateToolContext) -> CacheArtifactStore:
    if not context.workspace:
        raise CacheArtifactError("cache workspace is unavailable for this phase")
    return CacheArtifactStore(context.workspace)


def _cache_scope(context: PhaseStateToolContext, *, page_fingerprint: Any = "") -> dict[str, str]:
    raw = context.cache_scope if isinstance(context.cache_scope, dict) else {}
    return {
        "site_key": str(raw.get("site_key") or context.site_key or "").strip(),
        "phase": str(raw.get("phase") or "").strip(),
        "page_fingerprint": str(page_fingerprint or raw.get("page_fingerprint") or "").strip(),
    }


def _cache_dependency_versions(context: PhaseStateToolContext, keys: Any = None) -> dict[str, str]:
    available = context.cache_dependency_versions if isinstance(context.cache_dependency_versions, dict) else {}
    requested = [str(key).strip() for key in keys if str(key).strip()] if isinstance(keys, list) else []
    if not requested:
        requested = list(available.keys())
    return {key: str(available.get(key) or "").strip() for key in requested if str(available.get(key) or "").strip()}


def _cache_payload(*, status: str, data: dict[str, Any], message: str) -> dict[str, Any]:
    return {
        "isError": False,
        "structuredContent": {"status": status, **data},
        "content": [{"type": "text", "text": message}],
    }


def _cache_error(exc: Exception) -> dict[str, Any]:
    return {
        "isError": True,
        "error": str(exc),
        "structuredContent": {"status": "cache_error"},
        "content": [{"type": "text", "text": f"Cache operation failed: {exc}"}],
    }


def _cache_lookup_payload(*, context: PhaseStateToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        scope = _cache_scope(context, page_fingerprint=arguments.get("page_fingerprint"))
        candidates = _cache_store(context).lookup(
            scope=scope,
            dependency_versions=_cache_dependency_versions(context),
            kinds=arguments.get("kinds"),
            batch_id=context.batch_id,
            turn_id=context.turn_id,
        )
        return _cache_payload(
            status="lookup",
            data={"candidates": candidates, "scope": scope},
            message=f"Cache candidates: {len(candidates)}",
        )
    except Exception as exc:
        return _cache_error(exc)


def _cache_read_payload(*, context: PhaseStateToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        artifact = _cache_store(context).read(
            str(arguments.get("cache_id") or ""),
            scope=_cache_scope(context, page_fingerprint=arguments.get("page_fingerprint")),
            dependency_versions=_cache_dependency_versions(context),
            batch_id=context.batch_id,
            turn_id=context.turn_id,
        )
        return _cache_payload(
            status="read",
            data={"artifact": artifact},
            message=f"Cache artifact loaded: {artifact.get('cache_id')}",
        )
    except Exception as exc:
        return _cache_error(exc)


def _cache_propose_payload(*, context: PhaseStateToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        required = ("reuse_reason", "preconditions", "expected_benefit", "evidence")
        missing = [key for key in required if not arguments.get(key)]
        if missing:
            raise CacheArtifactError(f"cache proposal requires: {', '.join(missing)}")
        evidence = arguments.get("evidence") if isinstance(arguments.get("evidence"), list) else []
        artifact = _cache_store(context).propose(
            kind=str(arguments.get("kind") or ""),
            scope=_cache_scope(context, page_fingerprint=arguments.get("page_fingerprint")),
            dependency_versions=_cache_dependency_versions(context, arguments.get("dependency_keys")),
            content=arguments.get("content") if isinstance(arguments.get("content"), dict) else {},
            summary=str(arguments.get("summary") or ""),
            source_refs=[
                *(arguments.get("source_refs", []) if isinstance(arguments.get("source_refs"), list) else []),
                *evidence,
            ],
            proposal={
                "reuse_reason": str(arguments.get("reuse_reason") or ""),
                "preconditions": dict(arguments.get("preconditions") or {}),
                "expected_benefit": str(arguments.get("expected_benefit") or ""),
                "evidence": evidence,
            },
            batch_id=context.batch_id,
            turn_id=context.turn_id,
        )
        return _cache_payload(
            status="proposed",
            data={"artifact": artifact},
            message=f"Cache candidate proposed: {artifact.get('cache_id')}",
        )
    except Exception as exc:
        return _cache_error(exc)


def _cache_validate_payload(*, context: PhaseStateToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        artifact = _cache_store(context).validate(
            str(arguments.get("cache_id") or ""),
            status=str(arguments.get("status") or ""),
            summary=str(arguments.get("summary") or ""),
            batch_id=context.batch_id,
            turn_id=context.turn_id,
        )
        return _cache_payload(
            status="validated",
            data={"artifact": artifact},
            message=f"Cache artifact {artifact.get('cache_id')} marked {artifact.get('validation', {}).get('status')}",
        )
    except Exception as exc:
        return _cache_error(exc)


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
        or apply_state == DEFERRED_BY_RANK
    )


def _is_apply_review_complete_job_state(row: dict[str, Any] | None) -> bool:
    return _is_apply_terminal_job_state(row) or is_ranked_review_complete(row)


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
    if callable(list_jobs):
        try:
            before_rows = list_jobs(context.site_key, batch_id=context.batch_id)
        except TypeError:
            before_rows = list_jobs(context.site_key)
    else:
        before_rows = []
    before_ids = {str(row.get("job_id") or "") for row in before_rows if isinstance(row, dict)}
    if callable(classify_history_matches):
        try:
            history_matches = list(classify_history_matches(context.site_key, jobs, batch_id=context.batch_id))
        except TypeError:
            history_matches = list(classify_history_matches(context.site_key, jobs))
        except Exception:
            history_matches = []
    else:
        history_matches = []
    if not history_matches and callable(preview_new_flags):
        try:
            new_flags = list(preview_new_flags(context.site_key, jobs, batch_id=context.batch_id))
        except TypeError:
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
    list_run_jobs = getattr(context.site_store, "list_run_jobs", None)
    if callable(list_run_jobs):
        existing_rows = {
            str(row.get("job_id") or "").strip(): row
            for row in list_run_jobs(context.site_key, context.batch_id)
            if isinstance(row, dict) and str(row.get("job_id") or "").strip()
        }
    else:
        existing_rows = {}
    for job in jobs:
        job_id = str(job.get("job_id") or "").strip()
        validation_error = validate_ranking_pending_update({**dict(existing_rows.get(job_id) or {}), **job})
        if validation_error:
            return {
                "isError": True,
                "error": "invalid_ranking_pending_update",
                "structuredContent": {
                    "status": "validation_failed",
                    "job_id": job_id,
                    "reason": validation_error,
                },
                "content": [{"type": "text", "text": f"Job {job_id or '<unknown>'}: {validation_error}."}],
            }
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
    review_complete_ids = [
        str(row.get("job_id") or "").strip()
        for row in saved_rows
        if _is_apply_review_complete_job_state(row) and str(row.get("job_id") or "").strip()
    ]
    ranking_pending_ids = [
        str(row.get("job_id") or "").strip()
        for row in saved_rows
        if str(row.get("apply_state") or "").strip().lower() == RANKING_PENDING
        and str(row.get("job_id") or "").strip()
    ]
    return {
        "isError": False,
        "structuredContent": {
            "updated_count": len(updated_ids),
            "job_ids": updated_ids,
            "terminal_count": len(review_complete_ids),
            "terminal_job_ids": review_complete_ids,
            "application_terminal_count": len(terminal_ids),
            "application_terminal_job_ids": terminal_ids,
            "review_complete_count": len(review_complete_ids),
            "review_complete_job_ids": review_complete_ids,
            "ranking_pending_count": len(ranking_pending_ids),
            "ranking_pending_job_ids": ranking_pending_ids,
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
