"""Agent-visible CareerEng state/control tool declarations.

This module is the single schema and availability contract shared by provider,
MCP, CLI, and external-agent adapters. It deliberately contains no persistence
or workflow implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from careereng.platform.cache import CACHE_KINDS


PHASE_RESULT_TOOL = "phase_result"
RECORD_JOBS_TOOL = "record_jobs"
UPDATE_JOBS_TOOL = "update_jobs"
RECORD_APPLICATION_REVIEWS_TOOL = "record_application_reviews"
REQUEST_CONTEXT_TOOL = "request_context"
UPDATE_PHASE_MEMORY_TOOL = "update_phase_memory"
CACHE_LOOKUP_TOOL = "cache_lookup"
CACHE_READ_TOOL = "cache_read"
CACHE_PROPOSE_TOOL = "cache_propose"
CACHE_VALIDATE_TOOL = "cache_validate"

STATE_TOOL_NAMES = {
    PHASE_RESULT_TOOL,
    RECORD_JOBS_TOOL,
    UPDATE_JOBS_TOOL,
    RECORD_APPLICATION_REVIEWS_TOOL,
    REQUEST_CONTEXT_TOOL,
    UPDATE_PHASE_MEMORY_TOOL,
    CACHE_LOOKUP_TOOL,
    CACHE_READ_TOOL,
    CACHE_PROPOSE_TOOL,
    CACHE_VALIDATE_TOOL,
}

CACHE_TOOL_PHASES = frozenset({"channel_discovery", "job_retrieval", "job_filtering", "apply"})


def normalize_tool_name(value: str) -> str:
    return str(value or "").strip()


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


def cache_lookup_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "name": CACHE_LOOKUP_TOOL,
        "description": (
            "Find compatible reusable cache candidates for the current site and phase. "
            "Use a live-page fingerprint when the current page has been observed."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "page_fingerprint": {"type": "string"},
                "kinds": {"type": "array", "items": {"type": "string", "enum": sorted(CACHE_KINDS)}},
            },
            "required": ["page_fingerprint", "kinds"],
            "additionalProperties": False,
        },
    }


def cache_read_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "name": CACHE_READ_TOOL,
        "description": "Read the full content of one compatible cache candidate after deciding it may help this live phase.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "cache_id": {"type": "string"},
                "page_fingerprint": {"type": "string"},
            },
            "required": ["cache_id", "page_fingerprint"],
            "additionalProperties": False,
        },
    }


def cache_propose_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "name": CACHE_PROPOSE_TOOL,
        "description": (
            "Propose a reusable cache candidate from verified current evidence. This creates a candidate only; "
            "it does not change Skills or decide that the cache is valid for future pages."
        ),
        "strict": False,
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": sorted(CACHE_KINDS)},
                "page_fingerprint": {"type": "string"},
                "dependency_keys": {"type": "array", "items": {"type": "string"}},
                "summary": {"type": "string"},
                "source_refs": {"type": "array", "items": {"type": "string"}},
                "content": {"type": "object", "additionalProperties": True},
            },
            "required": ["kind", "page_fingerprint", "content"],
            "additionalProperties": False,
        },
    }


def cache_validate_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "name": CACHE_VALIDATE_TOOL,
        "description": "Record whether an existing cache candidate was validated, stale, or retired by current live evidence.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "cache_id": {"type": "string"},
                "status": {"type": "string", "enum": ["validated", "stale", "retired"]},
                "summary": {"type": "string"},
            },
            "required": ["cache_id", "status", "summary"],
            "additionalProperties": False,
        },
    }
@dataclass(frozen=True)
class StateToolSpec:
    """One static CareerEng tool and the phases where it is available."""

    name: str
    schema_factory: Callable[[], dict[str, Any]]
    phases: frozenset[str] = frozenset()
    always_available: bool = False

    def supports_phase(self, phase_slug: str) -> bool:
        return self.always_available or str(phase_slug or "").strip() in self.phases


class StateToolRegistry:
    """Single source of tool schemas and phase availability."""

    def __init__(self, specs: tuple[StateToolSpec, ...]) -> None:
        self._specs = {spec.name: spec for spec in specs}
        self._phase_order = tuple(spec.name for spec in specs if spec.name != PHASE_RESULT_TOOL)

    def contains(self, tool_name: str) -> bool:
        return normalize_tool_name(tool_name) in self._specs

    def schema(self, tool_name: str) -> dict[str, Any]:
        normalized = normalize_tool_name(tool_name)
        spec = self._specs.get(normalized)
        if spec is None:
            raise ValueError(f"unknown state tool: {tool_name}")
        return spec.schema_factory()

    def schemas_for_phase(self, phase_slug: str, *, include_phase_result: bool = False) -> list[dict[str, Any]]:
        schemas = [
            self.schema(tool_name)
            for tool_name in self._phase_order
            if self._specs[tool_name].supports_phase(phase_slug)
        ]
        if include_phase_result:
            schemas.append(self.schema(PHASE_RESULT_TOOL))
        return schemas


DEFAULT_STATE_TOOL_REGISTRY = StateToolRegistry(
    (
        StateToolSpec(PHASE_RESULT_TOOL, phase_result_tool_schema),
        StateToolSpec(UPDATE_PHASE_MEMORY_TOOL, update_phase_memory_tool_schema, always_available=True),
        StateToolSpec(CACHE_LOOKUP_TOOL, cache_lookup_tool_schema, CACHE_TOOL_PHASES),
        StateToolSpec(CACHE_READ_TOOL, cache_read_tool_schema, CACHE_TOOL_PHASES),
        StateToolSpec(CACHE_PROPOSE_TOOL, cache_propose_tool_schema, CACHE_TOOL_PHASES),
        StateToolSpec(CACHE_VALIDATE_TOOL, cache_validate_tool_schema, CACHE_TOOL_PHASES),
        StateToolSpec(
            RECORD_APPLICATION_REVIEWS_TOOL,
            record_application_reviews_tool_schema,
            frozenset({"application_status_review"}),
        ),
        StateToolSpec(RECORD_JOBS_TOOL, record_jobs_tool_schema, frozenset({"job_retrieval"})),
        StateToolSpec(UPDATE_JOBS_TOOL, update_jobs_tool_schema, frozenset({"apply"})),
        StateToolSpec(REQUEST_CONTEXT_TOOL, request_context_tool_schema, frozenset({"apply"})),
    )
)


def state_tool_schema(tool_name: str) -> dict[str, Any]:
    return DEFAULT_STATE_TOOL_REGISTRY.schema(tool_name)


def state_tool_schemas_for_phase(phase_slug: str, *, include_phase_result: bool = False) -> list[dict[str, Any]]:
    return DEFAULT_STATE_TOOL_REGISTRY.schemas_for_phase(phase_slug, include_phase_result=include_phase_result)
