"""Shared phase-runtime contract constants."""

from __future__ import annotations


PHASE_RESULT_TOOL = "phase_result"
RECORD_JOBS_TOOL = "record_jobs"
UPDATE_JOBS_TOOL = "update_jobs"
RECORD_APPLICATION_REVIEWS_TOOL = "record_application_reviews"
REQUEST_CONTEXT_TOOL = "request_context"
UPDATE_PHASE_MEMORY_TOOL = "update_phase_memory"

STATE_TOOL_NAMES = {
    PHASE_RESULT_TOOL,
    RECORD_JOBS_TOOL,
    UPDATE_JOBS_TOOL,
    RECORD_APPLICATION_REVIEWS_TOOL,
    REQUEST_CONTEXT_TOOL,
    UPDATE_PHASE_MEMORY_TOOL,
}


def normalize_tool_name(value: str) -> str:
    return str(value or "").strip()
