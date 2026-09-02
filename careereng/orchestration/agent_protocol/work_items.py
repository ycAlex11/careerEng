"""Generic worker-facing context catalog for persisted work items.

The catalog intentionally describes available resources without choosing which
ones an agent must use.  The agent owns that decision; this module only
enforces the persisted work item's scope.
"""

from __future__ import annotations

import json
from pathlib import Path
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
    "execution_diagnostics": "Recent objective runtime, browser, and transport diagnostics for this site work item.",
    "evolution_solution_request": "The persisted synthesis request and required proposal contract.",
    "evolution_evidence_pack": "The persisted evidence pack for the current synthesis request.",
    "evolution_summary_brief": "Small persisted run metadata and output contract for starting a synthesis without loading evidence.",
    "evolution_run_protocol": "The project-wide contract for completing one evolution run.",
    "evolution_proposal_schema": "The required shape and validation rules for an evolution proposal.",
    "evolution_strategy_router": "Guidance for choosing an evolution strategy from available evidence.",
}

_MAX_RESOURCE_CHARS = 16_000
_DEFAULT_RESOURCE_CHARS = 8_000
_EVOLUTION_DOCUMENTS = {
    "evolution_run_protocol": "docs/evolution/EVOLUTION_RUN_PROTOCOL.md",
    "evolution_proposal_schema": "docs/evolution/PROPOSAL_SCHEMA.md",
    "evolution_strategy_router": "docs/evolution/EVOLUTION_STRATEGY_ROUTER.md",
}


def work_item_id_from_payload(payload: dict[str, Any]) -> str:
    """Return the durable protocol identifier for a persisted work item."""

    return str(payload.get("work_order_id") or payload.get("handoff_id") or "").strip()


def build_work_item_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Build the minimal worker entry context and its resource catalog."""

    evolution = payload.get("evolution_solution") if isinstance(payload.get("evolution_solution"), dict) else {}
    if str(evolution.get("run_id") or "").strip():
        return {
            "work_item_id": work_item_id_from_payload(payload),
            "kind": "evolution_solution",
            "status": "active",
            "objective": {
                "phase": "evolution_summary",
                "title": "Apply the persisted site-run synthesis contract.",
            },
            "scope": {
                "site_key": str(payload.get("site_key") or ""),
                "batch_id": str(payload.get("batch_id") or ""),
                "session_id": str(payload.get("session_id") or ""),
                "turn_id": str(payload.get("turn_id") or ""),
                "evolution_run_id": str(evolution.get("run_id") or ""),
                "evolution_status": str(evolution.get("status") or "waiting_solution"),
            },
            "constraints": [
                "Use only the persisted synthesis request and evidence exposed by this work item.",
                "Do not create a browser runtime, inspect project files, or change Python code.",
                "Submit a structured proposal through CareerEng, apply it through CareerEng, then complete this synthesis.",
            ],
            "context_catalog": [
                {"resource_id": "evolution_summary_brief", "description": _RESOURCE_DESCRIPTIONS["evolution_summary_brief"]},
                {"resource_id": "evolution_solution_request", "description": _RESOURCE_DESCRIPTIONS["evolution_solution_request"]},
                {"resource_id": "evolution_evidence_pack", "description": _RESOURCE_DESCRIPTIONS["evolution_evidence_pack"]},
                {"resource_id": "evolution_run_protocol", "description": _RESOURCE_DESCRIPTIONS["evolution_run_protocol"]},
                {"resource_id": "evolution_proposal_schema", "description": _RESOURCE_DESCRIPTIONS["evolution_proposal_schema"]},
                {"resource_id": "evolution_strategy_router", "description": _RESOURCE_DESCRIPTIONS["evolution_strategy_router"]},
            ],
            "capabilities": {
                "submit_proposal": "careereng_submit_evolution_proposal",
                "apply_proposal": "careereng_apply_evolution_solution",
                "complete_solution": "careereng_complete_evolution_solution",
            },
        }

    phase_context = payload.get("current_phase_context")
    phase_context = phase_context if isinstance(phase_context, dict) else {}
    phase = phase_context.get("phase") if isinstance(phase_context.get("phase"), dict) else {}
    resource_ids = [name for name in _RESOURCE_DESCRIPTIONS if name in phase_context or name in {"state_tools", "execution_diagnostics"}]
    if str(payload.get("current_phase") or phase.get("slug") or "") == "apply":
        resource_ids.extend(["apply_facts", "full_cv", "full_persona", "history_view"])
    return {
        "work_item_id": work_item_id_from_payload(payload),
        "kind": "site_batch",
        "status": "active",
        "lease": {
            "context_revision": int(payload.get("context_revision") or 0),
            "site_revision": int(payload.get("site_revision") or 0),
            "control_epoch": int(payload.get("control_epoch") or 0),
        },
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
            "Pass lease.context_revision unchanged to every browser or state mutation.",
            "During apply, pass the current scope apply target unchanged when reporting the phase result.",
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


def read_work_item_resource(
    payload: dict[str, Any],
    resource_id: str,
    *,
    offset: int = 0,
    limit: int = _DEFAULT_RESOURCE_CHARS,
) -> dict[str, Any]:
    """Read one scoped resource, optionally as a bounded text slice."""

    requested = str(resource_id or "").strip()
    if requested not in _RESOURCE_DESCRIPTIONS:
        raise ValueError(f"unknown work-item resource: {requested or '<missing>'}")
    evolution = payload.get("evolution_solution") if isinstance(payload.get("evolution_solution"), dict) else {}
    if requested == "evolution_summary_brief":
        if not str(evolution.get("run_id") or "").strip():
            raise ValueError(f"work-item resource is not available: {requested}")
        return {
            "work_item_id": work_item_id_from_payload(payload),
            "resource_id": requested,
            "value": _evolution_summary_brief(evolution),
        }
    if requested in {"evolution_solution_request", "evolution_evidence_pack"}:
        if not str(evolution.get("run_id") or "").strip():
            raise ValueError(f"work-item resource is not available: {requested}")
        path_key = "solution_request" if requested == "evolution_solution_request" else "evidence_pack"
        path = Path(str(evolution.get(path_key) or ""))
        if not path.is_file():
            raise ValueError(f"evolution artifact is unavailable: {requested}")
        return _read_text_resource(payload, requested, path, offset=offset, limit=limit)
    if requested in _EVOLUTION_DOCUMENTS:
        if not str(evolution.get("run_id") or "").strip():
            raise ValueError(f"work-item resource is not available: {requested}")
        project_root = Path(__file__).resolve().parents[3]
        path = project_root / _EVOLUTION_DOCUMENTS[requested]
        if not path.is_file():
            raise ValueError(f"evolution document is unavailable: {requested}")
        return _read_text_resource(payload, requested, path, offset=offset, limit=limit)
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


def _read_text_resource(
    payload: dict[str, Any],
    resource_id: str,
    path: Path,
    *,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    """Return a bounded slice without deciding which source the agent needs."""

    start = max(0, int(offset or 0))
    size = min(_MAX_RESOURCE_CHARS, max(1, int(limit or _DEFAULT_RESOURCE_CHARS)))
    text = path.read_text(encoding="utf-8")
    end = min(len(text), start + size)
    return {
        "work_item_id": work_item_id_from_payload(payload),
        "resource_id": resource_id,
        "value": text[start:end],
        "offset": start,
        "next_offset": end if end < len(text) else None,
        "total_chars": len(text),
        "complete": end >= len(text),
    }


def _evolution_summary_brief(evolution: dict[str, Any]) -> dict[str, Any]:
    """Expose stored run metadata without loading its long-form evidence files."""

    solution_request = Path(str(evolution.get("solution_request") or ""))
    run_path = solution_request.parent / "run.json"
    run: dict[str, Any] = {}
    if run_path.is_file():
        try:
            loaded = json.loads(run_path.read_text(encoding="utf-8"))
            run = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            run = {}
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    return {
        "run_id": str(evolution.get("run_id") or ""),
        "run_status": str(evolution.get("status") or run.get("status") or "waiting_solution"),
        "candidate_id": str(run.get("candidate_id") or ""),
        "site_key": str(context.get("site_key") or ""),
        "batch_id": str(context.get("batch_id") or ""),
        "proposal_contract": dict(context.get("proposal_contract") or {}),
        "proposal_output_path": str(evolution.get("proposal_output_path") or ""),
        "available_detail_resources": [
            "evolution_run_protocol",
            "evolution_proposal_schema",
            "evolution_strategy_router",
            "evolution_solution_request",
            "evolution_evidence_pack",
        ],
    }
