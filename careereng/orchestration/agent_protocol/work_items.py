"""Generic worker-facing context catalog for persisted work items.

The catalog intentionally describes available resources without choosing which
ones an agent must use.  The agent owns that decision; this module only
enforces the persisted work item's scope.
"""

from __future__ import annotations

from typing import Any


_RESOURCE_DESCRIPTIONS = {
    "project_skill": "Project-level guidance relevant to this phase.",
    "site_skill": "Site-level guidance relevant to this phase.",
    "phase_memory": "Compact observations carried forward within this phase sequence.",
    "continuation": "Continuation details from a prior retained workflow state.",
    "cache_candidates": "Validated local cache candidates for this scoped phase.",
    "local_state": "Non-business identifiers and retained runtime scope for this phase.",
    "state_tools": "CareerEng state-tool schemas allowed for the current phase.",
    "apply_facts": "Current lightweight profile facts, available on demand during apply; re-read after user/profile changes.",
    "full_cv": "Current full CV text, available on demand when detailed evidence is needed.",
    "full_persona": "Current detailed persona/profile data, available on demand.",
    "history_view": "Current site-only batch history view, available on demand.",
}


def work_item_id_from_payload(payload: dict[str, Any]) -> str:
    """Return the durable protocol identifier for a persisted work item."""

    return str(payload.get("work_order_id") or payload.get("handoff_id") or "").strip()


def build_work_item_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Build the minimal worker entry context and its resource catalog."""

    phase_context = payload.get("current_phase_context")
    phase_context = phase_context if isinstance(phase_context, dict) else {}
    phase = phase_context.get("phase") if isinstance(phase_context.get("phase"), dict) else {}
    resource_ids = [name for name in _RESOURCE_DESCRIPTIONS if name in phase_context or name == "state_tools"]
    if str(payload.get("current_phase") or phase.get("slug") or "") == "apply":
        resource_ids.extend(["apply_facts", "full_cv", "full_persona", "history_view"])
    return {
        "work_item_id": work_item_id_from_payload(payload),
        "kind": "site_batch",
        "status": "active",
        "objective": {
            "phase": str(payload.get("current_phase") or phase.get("slug") or ""),
            "title": str(phase.get("title") or ""),
        },
        "scope": {
            "site_key": str(payload.get("site_key") or ""),
            "batch_id": str(payload.get("batch_id") or ""),
            "session_id": str(payload.get("session_id") or ""),
            "turn_id": str(payload.get("turn_id") or ""),
            "apply_target_job_ids": list(payload.get("apply_target_job_ids") or []),
        },
        "run_intent": (
            dict(payload.get("run_intent") or {})
            if isinstance(payload.get("run_intent"), dict)
            else {}
        ),
        "constraints": [
            "Operate only the retained runtime for this work item.",
            "Do not inspect project files or create a browser runtime for context.",
            "Use CareerEng MCP tools for browser and state changes.",
            "Treat run_intent as authoritative. Do not infer retrieval-only scope from an empty current apply target list.",
        ],
        "context_catalog": [
            {"resource_id": resource_id, "description": _RESOURCE_DESCRIPTIONS[resource_id]}
            for resource_id in resource_ids
        ],
        "capabilities": {
            "browser_tools": "careereng_work_item_list_browser_tools / careereng_work_item_call_browser_tool",
            "browser_sequence": "careereng_work_item_run_browser_sequence",
            "state_tools": "careereng_work_item_list_state_tools / careereng_work_item_call_state_tool",
            "phase_result": "careereng_work_item_phase_result",
        },
    }


def read_work_item_resource(payload: dict[str, Any], resource_id: str) -> dict[str, Any]:
    """Read one catalog resource from a work item without widening its scope."""

    requested = str(resource_id or "").strip()
    if requested not in _RESOURCE_DESCRIPTIONS:
        raise ValueError(f"unknown work-item resource: {requested or '<missing>'}")
    phase_context = payload.get("current_phase_context")
    phase_context = phase_context if isinstance(phase_context, dict) else {}
    if requested == "state_tools":
        value: Any = list(payload.get("state_tools") or [])
    elif requested in {"apply_facts", "full_cv", "full_persona", "history_view"}:
        raise ValueError(f"work-item resource requires the active runtime: {requested}")
    else:
        if requested not in phase_context:
            raise ValueError(f"work-item resource is not available: {requested}")
        value = phase_context.get(requested)
    return {
        "work_item_id": work_item_id_from_payload(payload),
        "resource_id": requested,
        "value": value,
    }
