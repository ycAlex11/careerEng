"""External-agent work order packaging."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

from .contracts import AGENT_BRIDGE_MODE, AGENT_BRIDGE_STATUS
from .state import state_tool_commands
from careereng.orchestration.context import BrowserPhaseMemory, build_phase_context
from careereng.platform.sessions import PhaseSession, write_phase_session
from careereng.platform.observability import PerformanceRecorder
from careereng.platform.cache import CacheArtifactStore
from careereng.evolution.memory_units import active_run_local_guidance
from careereng.orchestration.agent_protocol.state_tools import state_tool_schemas_for_phase
from careereng.orchestration.agent_protocol.work_item_store import WorkItemStore
from careereng.utils import ensure_dir, make_id, now_iso, read_json, safe_file_stem, write_json


def _active_phase_evolution_guidance(*, workspace: Path, site_key: str, batch_id: str, phase: str) -> str:
    try:
        return active_run_local_guidance(
            workspace=workspace,
            site_key=site_key,
            batch_id=batch_id,
            phase=phase,
        )
    except Exception:
        return ""


@dataclass(frozen=True)
class AgentBridgeWorkOrder:
    payload_path: Path
    markdown_path: Path
    current_phase: str
    message: str


def load_active_phase_context(payload_path: Path | str) -> dict[str, Any]:
    """Return the current agent-facing context from an existing work-order payload.

    The payload remains the durable recovery artifact. This envelope is the
    transport shape returned to an external agent so it does not need to read a
    workspace file before it can act on the current phase.
    """

    path = Path(payload_path).expanduser()
    if not path.is_file():
        return {}
    payload = read_json(path)
    if not isinstance(payload, dict):
        return {}
    phase_context = payload.get("current_phase_context")
    if not isinstance(phase_context, dict):
        return {}
    return {
        "work_order_id": str(payload.get("work_order_id") or payload.get("handoff_id") or ""),
        "execution_mode": str(payload.get("execution_mode") or ""),
        "agent_name": str(payload.get("agent_name") or ""),
        "site_key": str(payload.get("site_key") or ""),
        "site_name": str(payload.get("site_name") or ""),
        "session_id": str(payload.get("session_id") or ""),
        "turn_id": str(payload.get("turn_id") or ""),
        "batch_id": str(payload.get("batch_id") or ""),
        "current_phase": str(payload.get("current_phase") or ""),
        "phase_context": phase_context,
        "browser_tool_commands": dict(payload.get("browser_tool_commands") or {}),
        "state_tool_commands": dict(payload.get("state_tool_commands") or {}),
        "state_tools": list(payload.get("state_tools") or []),
        "updated_at": str(payload.get("updated_at") or payload.get("created_at") or ""),
        "payload_path": str(path.resolve()),
        "work_order_path": str(payload.get("markdown_path") or ""),
    }


def advance_browser_agent_work_order(
    *,
    workspace: Path,
    payload_path: Path,
    phase_session_path: Path,
    next_phase: str,
) -> AgentBridgeWorkOrder:
    """Refresh an existing external-agent work order for its next declared phase.

    This is adapter packaging only. The generic engine decides the transition;
    the owning career domain decides whether another work item exists.
    """

    workspace = Path(workspace).resolve()
    payload_path = Path(payload_path)
    phase_session_path = Path(phase_session_path)
    payload = read_json(payload_path)
    session_payload = read_json(phase_session_path)
    normalized_phase = str(next_phase or "").strip()
    phase_rows = payload.get("phases") if isinstance(payload.get("phases"), list) else []
    current_phase_row = next(
        (
            row
            for row in phase_rows
            if isinstance(row, dict) and str(row.get("slug") or "").strip() == normalized_phase
        ),
        None,
    )
    if not normalized_phase or not isinstance(current_phase_row, dict):
        raise ValueError(f"phase is not declared by the work order: {next_phase or '<missing>'}")

    phase_memory = BrowserPhaseMemory.from_payload(session_payload.get("phase_memory"))
    cache_dependency_versions = payload.get("cache_dependency_versions") if isinstance(payload.get("cache_dependency_versions"), dict) else {}
    cache_candidates = CacheArtifactStore(workspace).lookup(
        scope={"site_key": str(payload.get("site_key") or ""), "phase": normalized_phase},
        dependency_versions=cache_dependency_versions,
        batch_id=str(payload.get("batch_id") or ""),
        turn_id=str(payload.get("turn_id") or ""),
    )
    phase_context = build_phase_context(
        SimpleNamespace(
            slug=str(current_phase_row.get("slug") or ""),
            title=str(current_phase_row.get("title") or ""),
            project_text=str(current_phase_row.get("project_text") or ""),
            site_text=str(current_phase_row.get("site_text") or ""),
        ),
        phase_memory=phase_memory,
        continuation=(
            payload.get("continuation_context") if isinstance(payload.get("continuation_context"), dict) else {}
        ),
        local_state={
            "site_key": str(payload.get("site_key") or ""),
            "entry_url": str(payload.get("entry_url") or ""),
            "batch_id": str(payload.get("batch_id") or ""),
            "session_id": str(payload.get("session_id") or ""),
            "turn_id": str(payload.get("turn_id") or ""),
            "apply_target_job_ids": list(payload.get("apply_target_job_ids") or []),
            "run_intent": payload.get("run_intent") if isinstance(payload.get("run_intent"), dict) else {},
            "cache_dependency_versions": cache_dependency_versions,
            "active_run_local_guidance": _active_phase_evolution_guidance(
                workspace=workspace,
                site_key=str(payload.get("site_key") or ""),
                batch_id=str(payload.get("batch_id") or ""),
                phase=normalized_phase,
            ),
        },
        cache_candidates=cache_candidates,
    ).as_dict()
    state_commands = state_tool_commands(str(payload.get("site_key") or ""), phase=normalized_phase)
    state_tools = state_tool_schemas_for_phase(normalized_phase, include_phase_result=True)

    work_item_id = str(payload.get("work_order_id") or payload.get("handoff_id") or "").strip()
    if not work_item_id:
        raise ValueError("external-agent work order has no stable work_item_id")
    # A work item owns one site task for the lifetime of its batch. Advancing a
    # phase refreshes its scoped context in place; it must not replace the
    # Codex thread that is carrying the task's live reasoning state.
    next_context_revision = max(
        int(payload.get("context_revision") or 0),
        int(session_payload.get("context_revision") or 0),
    ) + 1
    payload.update(
        {
            "updated_at": now_iso(),
            "context_revision": next_context_revision,
            "worker_state": "active",
            "current_phase": normalized_phase,
            "current_phase_context": phase_context,
            "state_tool_commands": state_commands,
            "state_tools": state_tools,
        }
    )
    session_payload.update(
        {
            "updated_at": now_iso(),
            "context_revision": next_context_revision,
            "worker_state": "active",
            "current_phase": normalized_phase,
            "phase_context": phase_context,
            "state_tool_commands": state_commands,
            "state_tools": state_tools,
        }
    )
    write_json(payload_path, payload)
    write_json(phase_session_path, session_payload)
    WorkItemStore(workspace).register(payload_path, event="phase_advanced")

    markdown_path = Path(payload.get("markdown_path") or "")
    if not markdown_path.is_absolute():
        markdown_path = payload_path.parent / "work_order.md"
    markdown_path.write_text(_render_browser_work_order(payload), encoding="utf-8")
    return AgentBridgeWorkOrder(
        payload_path=payload_path,
        markdown_path=markdown_path,
        current_phase=normalized_phase,
        message=(
            f"External-agent work order advanced to phase={normalized_phase}. "
            f"Continue with the retained browser runtime using {markdown_path}."
        ),
    )


def refresh_browser_agent_work_order(
    *,
    workspace: Path,
    payload_path: Path,
    phase_session_path: Path,
    entry_url: str,
    phase_slugs: tuple[str, ...],
    phases: Iterable[Any],
    apply_target_job_ids: tuple[str, ...] | None = None,
    continuation_context: dict[str, Any] | None = None,
    tool_commands: dict[str, str] | None = None,
    cache_candidates: list[dict[str, Any]] | None = None,
    cache_dependency_versions: dict[str, Any] | None = None,
    apply_initial_facts: dict[str, Any] | None = None,
    skill_snapshot: dict[str, Any] | None = None,
) -> AgentBridgeWorkOrder:
    """Reuse a site-batch work item for the next declared browser sequence."""

    workspace = Path(workspace).resolve()
    payload_path = Path(payload_path)
    phase_session_path = Path(phase_session_path)
    payload = read_json(payload_path)
    session_payload = read_json(phase_session_path)
    if not isinstance(payload, dict) or not isinstance(session_payload, dict):
        raise ValueError("agent bridge work order is unavailable")
    phase_rows = list(phases)
    current_phase_row = phase_rows[0] if phase_rows else None
    current_phase = str(getattr(current_phase_row, "slug", "") or (phase_slugs[0] if phase_slugs else "")).strip()
    if not current_phase or current_phase_row is None:
        raise ValueError("refreshed agent bridge work order requires a declared first phase")
    versions = dict(cache_dependency_versions or payload.get("cache_dependency_versions") or {})
    phase_memory = BrowserPhaseMemory()
    phase_context = build_phase_context(
        current_phase_row,
        phase_memory=phase_memory,
        continuation=continuation_context,
        local_state={
            "site_key": str(payload.get("site_key") or ""),
            "entry_url": str(entry_url or ""),
            "batch_id": str(payload.get("batch_id") or ""),
            "session_id": str(payload.get("session_id") or ""),
            "turn_id": str(payload.get("turn_id") or ""),
            "apply_target_job_ids": list(apply_target_job_ids or ()),
            "run_intent": dict(payload.get("run_intent") or {}),
            "cache_dependency_versions": versions,
            "active_run_local_guidance": _active_phase_evolution_guidance(
                workspace=workspace,
                site_key=str(payload.get("site_key") or ""),
                batch_id=str(payload.get("batch_id") or ""),
                phase=current_phase,
            ),
        },
        cache_candidates=cache_candidates,
    ).as_dict()
    resolved_apply_facts = dict(apply_initial_facts or payload.get("apply_initial_facts") or {})
    resolved_skill_snapshot = dict(
        payload.get("skill_snapshot")
        if isinstance(payload.get("skill_snapshot"), dict)
        else (skill_snapshot or {})
    )
    state_commands = state_tool_commands(str(payload.get("site_key") or ""), phase=current_phase)
    state_tools = state_tool_schemas_for_phase(current_phase, include_phase_result=True)
    phase_payload = [
        {
            "slug": str(getattr(phase, "slug", "") or ""),
            "title": str(getattr(phase, "title", "") or ""),
            "project_text": str(getattr(phase, "project_text", "") or ""),
            "site_text": str(getattr(phase, "site_text", "") or ""),
        }
        for phase in phase_rows
    ]
    if continuation_context:
        continuation_path_value = str(payload.get("continuation_context_path") or "").strip()
        if continuation_path_value:
            continuation_path = Path(continuation_path_value)
            if not continuation_path.is_absolute():
                continuation_path = workspace / continuation_path
        else:
            continuation_path = payload_path.parent / "continuation_context.json"
        write_json(continuation_path, dict(continuation_context))
        payload["continuation_context_path"] = _workspace_relative(workspace, continuation_path)
    now = now_iso()
    next_context_revision = max(
        int(payload.get("context_revision") or 0),
        int(session_payload.get("context_revision") or 0),
    ) + 1
    for row in (payload, session_payload):
        row.update(
            {
                "updated_at": now,
                "context_revision": next_context_revision,
                "worker_state": "active",
                "entry_url": str(entry_url or ""),
                "phase_slugs": list(phase_slugs),
                "current_phase": current_phase,
                "apply_target_job_ids": list(apply_target_job_ids or ()),
                "continuation_context": dict(continuation_context or {}),
                "phase_context": phase_context,
                "current_phase_context": phase_context,
                "phase_memory": phase_memory.as_payload(),
                "browser_tool_commands": dict(tool_commands or payload.get("browser_tool_commands") or {}),
                "state_tool_commands": state_commands,
                "state_tools": state_tools,
                "apply_initial_facts": resolved_apply_facts,
            }
        )
    payload["phases"] = phase_payload
    payload["cache_dependency_versions"] = versions
    payload["skill_snapshot"] = resolved_skill_snapshot
    write_json(payload_path, payload)
    write_json(phase_session_path, session_payload)
    WorkItemStore(workspace).register(payload_path, event="work_item_refreshed")
    markdown_path = Path(str(payload.get("markdown_path") or ""))
    if not markdown_path.is_absolute():
        markdown_path = payload_path.parent / "work_order.md"
    markdown_path.write_text(_render_browser_work_order(payload), encoding="utf-8")
    return AgentBridgeWorkOrder(
        payload_path=payload_path,
        markdown_path=markdown_path,
        current_phase=current_phase,
        message=f"External-agent site-batch work item refreshed for phase={current_phase}.",
    )


def set_browser_agent_work_order_state(
    *,
    workspace: Path | None = None,
    payload_path: Path,
    phase_session_path: Path,
    worker_state: str,
) -> None:
    """Persist lifecycle state without changing the work-item scope."""

    payload = read_json(Path(payload_path))
    session_payload = read_json(Path(phase_session_path))
    if not isinstance(payload, dict) or not isinstance(session_payload, dict):
        raise ValueError("agent bridge work order is unavailable")
    updated_at = now_iso()
    for row in (payload, session_payload):
        row["worker_state"] = str(worker_state or "").strip()
        row["updated_at"] = updated_at
    write_json(Path(payload_path), payload)
    write_json(Path(phase_session_path), session_payload)
    if workspace is not None:
        WorkItemStore(workspace).register(payload_path, event=f"state:{worker_state}")


def activate_browser_agent_evolution_solution(
    *,
    workspace: Path | None = None,
    payload_path: Path,
    phase_session_path: Path,
    run_id: str,
    solution_request: str,
    proposal_output_path: str,
    evidence_pack: str = "",
    solution_status: str = "waiting_solution",
) -> None:
    """Make an already-created evolution request the next turn of one worker.

    The browser work item keeps its stable identity and retained Codex thread.
    This only refreshes its context so the generic worker coordinator starts a
    follow-up turn after the current browser turn completes.
    """

    payload = read_json(Path(payload_path))
    session_payload = read_json(Path(phase_session_path))
    if not isinstance(payload, dict) or not isinstance(session_payload, dict):
        raise ValueError("agent bridge work order is unavailable")
    request = {
        "run_id": str(run_id or "").strip(),
        "solution_request": str(solution_request or "").strip(),
        "proposal_output_path": str(proposal_output_path or "").strip(),
        "evidence_pack": str(evidence_pack or Path(str(solution_request or "")).with_name("evidence_pack.md")).strip(),
        "status": str(solution_status or "waiting_solution").strip(),
    }
    if not request["run_id"] or not request["solution_request"] or not request["proposal_output_path"]:
        raise ValueError("evolution solution handoff requires run_id and artifact paths")
    now = now_iso()
    for row in (payload, session_payload):
        row.update(
            {
                "updated_at": now,
                "context_revision": int(row.get("context_revision") or 0) + 1,
                "worker_state": "active",
                "current_phase": "evolution_summary",
                "current_phase_context": {
                    "phase": {"slug": "evolution_summary", "title": "Evolution summary"},
                },
                "evolution_solution": request,
            }
        )
    write_json(Path(payload_path), payload)
    write_json(Path(phase_session_path), session_payload)
    if workspace is not None:
        WorkItemStore(workspace).register(payload_path, event="evolution_solution_activated")


def persist_browser_agent_phase_memory(
    *,
    payload_path: Path,
    phase_session_path: Path,
    phase_memory: dict[str, Any],
) -> None:
    """Persist generic run-local memory and refresh the active agent context."""

    payload_path = Path(payload_path)
    phase_session_path = Path(phase_session_path)
    payload = read_json(payload_path)
    session_payload = read_json(phase_session_path)
    if not isinstance(payload, dict) or not isinstance(session_payload, dict):
        raise ValueError("agent bridge phase session is unavailable")

    memory_payload = dict(phase_memory or {})
    memory_text = BrowserPhaseMemory.from_payload(memory_payload).phase_memory_text()
    for row in (payload, session_payload):
        row["updated_at"] = now_iso()
        row["phase_memory"] = memory_payload
        phase_context = row.get("current_phase_context")
        if not isinstance(phase_context, dict):
            phase_context = row.get("phase_context") if isinstance(row.get("phase_context"), dict) else {}
        phase_context = dict(phase_context)
        phase_context["phase_memory"] = memory_text
        row["current_phase_context"] = phase_context
        row["phase_context"] = phase_context

    write_json(payload_path, payload)
    write_json(phase_session_path, session_payload)
    markdown_path = Path(str(payload.get("markdown_path") or ""))
    if not markdown_path.is_absolute():
        markdown_path = payload_path.parent / "work_order.md"
    markdown_path.write_text(_render_browser_work_order(payload), encoding="utf-8")


def persist_browser_agent_checkpoint(
    *,
    payload_path: Path,
    phase_session_path: Path,
    checkpoint: dict[str, Any],
) -> None:
    """Persist an executor-observed checkpoint without interpreting the page.

    This is intentionally transport-neutral: the browser executor records what
    it just did, while the next agent turn decides whether it needs a new
    observation or a recovery action.
    """

    payload_path = Path(payload_path)
    phase_session_path = Path(phase_session_path)
    payload = read_json(payload_path)
    session_payload = read_json(phase_session_path)
    if not isinstance(payload, dict) or not isinstance(session_payload, dict):
        raise ValueError("agent bridge phase session is unavailable")

    normalized_checkpoint = dict(checkpoint or {})
    for row in (payload, session_payload):
        row["updated_at"] = now_iso()
        row["last_browser_checkpoint"] = normalized_checkpoint
        phase_context = row.get("current_phase_context")
        if not isinstance(phase_context, dict):
            phase_context = row.get("phase_context") if isinstance(row.get("phase_context"), dict) else {}
        phase_context = dict(phase_context)
        phase_context["browser_checkpoint"] = normalized_checkpoint
        row["current_phase_context"] = phase_context
        row["phase_context"] = phase_context

    write_json(payload_path, payload)
    write_json(phase_session_path, session_payload)


def create_browser_agent_work_order(
    *,
    workspace: Path,
    site_store: Any,
    site_key: str,
    site_name: str,
    entry_url: str,
    session_id: str,
    turn_id: str,
    batch_id: str,
    resume: bool,
    phase_slugs: tuple[str, ...],
    phases: Iterable[Any],
    project_skill_path: Path,
    site_skill_path: Path,
    apply_target_job_ids: tuple[str, ...] | None = None,
    continuation_context: dict[str, Any] | None = None,
    tool_commands: dict[str, str] | None = None,
    state_commands: dict[str, str] | None = None,
    agent_name: str = "codex",
    cache_candidates: list[dict[str, Any]] | None = None,
    cache_dependency_versions: dict[str, Any] | None = None,
    apply_initial_facts: dict[str, Any] | None = None,
    skill_snapshot: dict[str, Any] | None = None,
) -> AgentBridgeWorkOrder:
    workspace = Path(workspace).resolve()
    phase_rows = list(phases)
    current_phase = phase_rows[0].slug if phase_rows else (phase_slugs[0] if phase_slugs else "")
    current_phase_row = phase_rows[0] if phase_rows else None
    work_dir = _work_order_dir(workspace=workspace, site_key=site_key, batch_id=batch_id, turn_id=turn_id)
    payload_path = work_dir / "payload.json"
    markdown_path = work_dir / "work_order.md"

    continuation_context_path = ""
    if continuation_context:
        continuation_path = work_dir / "continuation_context.json"
        write_json(continuation_path, continuation_context)
        continuation_context_path = _workspace_relative(workspace, continuation_path)

    work_order_id = make_id("agent_bridge")
    state_tools = state_tool_schemas_for_phase(current_phase, include_phase_result=True)
    resolved_state_commands = state_commands or state_tool_commands(site_key, phase=current_phase)
    phase_memory = BrowserPhaseMemory()
    run_intent = {}
    if isinstance(continuation_context, dict):
        candidate = continuation_context.get("run_intent")
        if isinstance(candidate, dict):
            run_intent = dict(candidate)
    phase_context = build_phase_context(
        current_phase_row,
        phase_memory=phase_memory,
        continuation=continuation_context,
        local_state={
            "site_key": site_key,
            "entry_url": entry_url,
            "batch_id": batch_id,
            "session_id": session_id,
            "turn_id": turn_id,
            "apply_target_job_ids": list(apply_target_job_ids or ()),
            "run_intent": run_intent,
            "cache_dependency_versions": dict(cache_dependency_versions or {}),
            "active_run_local_guidance": _active_phase_evolution_guidance(
                workspace=workspace,
                site_key=site_key,
                batch_id=batch_id,
                phase=current_phase,
            ),
        },
        cache_candidates=cache_candidates,
    ).as_dict()
    resolved_apply_facts = dict(apply_initial_facts or {})
    phase_session = PhaseSession(
        site_key=site_key,
        site_name=site_name,
        entry_url=entry_url,
        session_id=session_id,
        turn_id=turn_id,
        batch_id=batch_id,
        current_phase=current_phase,
        phase_slugs=tuple(phase_slugs or ()),
        apply_target_job_ids=tuple(apply_target_job_ids or ()),
        continuation_context=continuation_context or {},
        phase_context=phase_context,
        phase_memory=phase_memory.as_payload(),
        browser_tool_commands=tool_commands or {},
        state_tool_commands=resolved_state_commands,
        state_tools=state_tools,
    )
    phase_session_path = write_phase_session(phase_session, workspace=workspace, path=work_dir / "phase_session.json")
    payload: dict[str, Any] = {
        "work_order_id": work_order_id,
        "handoff_id": work_order_id,  # Legacy readers may still expect this field.
        "created_at": now_iso(),
        "execution_mode": AGENT_BRIDGE_MODE,
        "execution_backend": str(run_intent.get("execution_backend") or "codex"),
        "agent_name": str(agent_name or "external_agent"),
        "site_key": site_key,
        "site_name": site_name,
        "entry_url": entry_url,
        "session_id": session_id,
        "turn_id": turn_id,
        "batch_id": batch_id,
        "resume": bool(resume),
        "context_revision": 1,
        "worker_state": "active",
        "phase_slugs": list(phase_slugs),
        "current_phase": current_phase,
        "apply_target_job_ids": list(apply_target_job_ids or ()),
        "run_intent": run_intent,
        "continuation_context": continuation_context or {},
        "continuation_context_path": continuation_context_path,
        "current_phase_context": phase_context,
        "phase_memory": phase_memory.as_payload(),
        "cache_dependency_versions": dict(cache_dependency_versions or {}),
        "skill_snapshot": dict(skill_snapshot or {}),
        "apply_initial_facts": resolved_apply_facts,
        "tool_commands": tool_commands or {},
        "browser_tool_commands": tool_commands or {},
        "state_tool_commands": resolved_state_commands,
        "state_tools": state_tools,
        "phase_session_path": _workspace_relative(workspace, phase_session_path),
        "project_skill_path": _workspace_relative(workspace, project_skill_path),
        "site_skill_path": _workspace_relative(workspace, site_skill_path),
        "phases": [
            {
                "slug": phase.slug,
                "title": phase.title,
                "project_text": phase.project_text,
                "site_text": phase.site_text,
            }
            for phase in phase_rows
        ],
    }
    payload["payload_path"] = _workspace_relative(workspace, payload_path)
    payload["markdown_path"] = _workspace_relative(workspace, markdown_path)
    write_json(payload_path, payload)
    markdown_path.write_text(_render_browser_work_order(payload), encoding="utf-8")
    WorkItemStore(workspace).register(payload_path, event="created")
    PerformanceRecorder(workspace).record(
        backend="external_agent",
        operation="agent_context",
        site_key=site_key,
        batch_id=batch_id,
        phase=current_phase,
        status="ok",
        observation_kind="compact",
        agent_input_bytes=len(json.dumps(phase_context, ensure_ascii=False).encode("utf-8")),
    )

    site_store.save_browser_session(
        site_key,
        {
            "browser_status": AGENT_BRIDGE_STATUS,
            "pending_action": AGENT_BRIDGE_STATUS,
            "active_run_id": turn_id,
            "agent_bridge_session_id": session_id,
            "agent_bridge_batch_id": batch_id,
            "agent_bridge_turn_id": turn_id,
            "agent_bridge_current_phase": current_phase,
            "agent_bridge_apply_target_job_ids": list(apply_target_job_ids or ()),
            "last_known_url": entry_url or "",
            "current_trace_ref": "",
            "current_step_id": f"{current_phase}:agent_bridge" if current_phase else "agent_bridge",
            "current_step_status": "waiting_external_agent",
            "last_step_error": "",
            "phase_session_path": str(phase_session_path),
            "agent_bridge_payload_path": str(payload_path),
            "agent_bridge_work_order_path": str(markdown_path),
            "codex_handoff_path": str(payload_path),
            "codex_handoff_markdown_path": str(markdown_path),
        },
    )
    site_store.append_event(
        site_key,
        "browser.agent_bridge.work_order_created",
        {
            "turn_id": turn_id,
            "batch_id": batch_id,
            "phase": current_phase,
            "payload_path": str(payload_path),
            "work_order_path": str(markdown_path),
            "phase_session_path": str(phase_session_path),
            "agent_name": str(agent_name or "external_agent"),
        },
    )
    message = (
        "Browser execution is waiting for external agent bridge. "
        f"work_order={markdown_path} payload={payload_path}"
    )
    return AgentBridgeWorkOrder(
        payload_path=payload_path,
        markdown_path=markdown_path,
        current_phase=current_phase,
        message=message,
    )


def _work_order_dir(*, workspace: Path, site_key: str, batch_id: str, turn_id: str) -> Path:
    batch_key = safe_file_stem(batch_id or "adhoc_batch")
    turn_key = safe_file_stem(turn_id or make_id("turn"))
    return ensure_dir(workspace / "agent_bridge" / "browser" / safe_file_stem(site_key) / f"{batch_key}_{turn_key}")


def _workspace_relative(workspace: Path, path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(workspace))
    except Exception:
        return str(path)


def _render_browser_work_order(payload: dict[str, Any]) -> str:
    phase_lines = "\n".join(
        f"- `{phase.get('slug')}`: {phase.get('title')}"
        for phase in payload.get("phases", [])
        if isinstance(phase, dict)
    )
    target_lines = "\n".join(f"- `{item}`" for item in payload.get("apply_target_job_ids", [])) or "- `(none)`"
    continuation_context = payload.get("continuation_context")
    phase_context = payload.get("current_phase_context") if isinstance(payload.get("current_phase_context"), dict) else {}
    phase_details = phase_context.get("phase") if isinstance(phase_context.get("phase"), dict) else {}
    project_skill_text = str(phase_context.get("project_skill") or "").strip()
    site_skill_text = str(phase_context.get("site_skill") or "").strip()
    phase_memory_text = str(phase_context.get("phase_memory") or "").strip()
    apply_runtime_hints = payload.get("apply_initial_facts") if isinstance(payload.get("apply_initial_facts"), dict) else {}
    if not apply_runtime_hints and isinstance(phase_context.get("apply_facts"), dict):
        # Read-only compatibility for older persisted work orders. New work
        # orders never place profile facts in the phase context.
        apply_runtime_hints = dict(phase_context["apply_facts"])
    browser_checkpoint = phase_context.get("browser_checkpoint") if isinstance(phase_context.get("browser_checkpoint"), dict) else {}
    cache_candidates = phase_context.get("cache_candidates") if isinstance(phase_context.get("cache_candidates"), list) else []
    cache_protocol = str(phase_context.get("cache_protocol") or "").strip()
    continuation_payload = phase_context.get("continuation") if isinstance(phase_context.get("continuation"), dict) else {}
    local_state = phase_context.get("local_state") if isinstance(phase_context.get("local_state"), dict) else {}
    tool_commands = payload.get("tool_commands") if isinstance(payload.get("tool_commands"), dict) else {}
    continuation_line = (
        f"- Continuation Context: `{payload.get('continuation_context_path')}`"
        if continuation_context
        else "- Continuation Context: `(none)`"
    )
    preferred_tools = str(tool_commands.get("tools") or "").strip()
    preferred_snapshot = str(tool_commands.get("snapshot") or "").strip()
    preferred_call = str(tool_commands.get("call") or "").strip()
    preferred_sequence = str(tool_commands.get("sequence") or "").strip()
    legacy_tools = str(tool_commands.get("legacy_tools") or "").strip()
    state_commands = payload.get("state_tool_commands") if isinstance(payload.get("state_tool_commands"), dict) else {}
    tool_command_lines = []
    if preferred_tools:
        tool_command_lines.append(f"- List tools: `{preferred_tools}`")
    if preferred_snapshot:
        tool_command_lines.append(f"- Snapshot: `{preferred_snapshot}`")
    if preferred_call:
        tool_command_lines.append(f"- Call tool: `{preferred_call}`")
    if preferred_sequence:
        tool_command_lines.append(f"- Sequence: `{preferred_sequence}`")
    if legacy_tools:
        tool_command_lines.append(f"- Legacy alias: `{legacy_tools}`")
    tool_command_text = "\n".join(tool_command_lines) or "- `(not available)`"
    state_tool_lines = []
    if state_commands.get("tools"):
        state_tool_lines.append(f"- List state tools: `{state_commands.get('tools')}`")
    if state_commands.get("call"):
        state_tool_lines.append(f"- Call state tool: `{state_commands.get('call')}`")
    if state_commands.get("phase_result"):
        state_tool_lines.append(f"- Phase result: `{state_commands.get('phase_result')}`")
    state_tool_text = "\n".join(state_tool_lines) or "- `(not available)`"
    return (
        "# Browser Agent Work Order\n\n"
        f"CareerEng is using `browser.execution_mode={AGENT_BRIDGE_MODE}` with `{payload.get('agent_name')}` as the external agent brain.\n"
        "CareerEng starts and retains the existing Playwright MCP browser runtime. The external agent should use the commands below to operate that retained runtime instead of opening another browser profile.\n\n"
        "## Scope\n\n"
        f"- Site: `{payload.get('site_key')}` / {payload.get('site_name')}\n"
        f"- Entry URL: {payload.get('entry_url')}\n"
        f"- Batch: `{payload.get('batch_id')}`\n"
        f"- Turn: `{payload.get('turn_id')}`\n"
        f"- Resume: `{str(payload.get('resume')).lower()}`\n"
        f"{continuation_line}\n\n"
        "## Requested Phases\n\n"
        f"{phase_lines or '- `(none)`'}\n\n"
        "## Apply Targets\n\n"
        f"{target_lines}\n\n"
        "## Current Phase Context\n\n"
        f"- Phase: `{phase_details.get('slug') or payload.get('current_phase')}` / {phase_details.get('title') or '(untitled)'}\n"
        f"- Local State: `{local_state}`\n"
        f"- Continuation: `{continuation_payload or '(none)'}`\n\n"
        "### Current Site Skill\n\n"
        f"{site_skill_text or '(none)'}\n\n"
        "### Current Project Skill\n\n"
        f"{project_skill_text or '(none)'}\n\n"
        "### Current Phase Memory\n\n"
        f"{phase_memory_text or '(none)'}\n\n"
        "### Lightweight Apply Facts\n\n"
        "`apply_facts` is available on demand through the work-item context. Request it only when the live form needs profile data; it is refreshed when the source profile changes.\n\n"
        "### Apply Execution Hints\n\n"
        f"{json.dumps(apply_runtime_hints, ensure_ascii=False, indent=2) if apply_runtime_hints else '(none)'}\n\n"
        "### Recent Browser Checkpoint\n\n"
        f"{json.dumps(browser_checkpoint, ensure_ascii=False, indent=2) if browser_checkpoint else '(none)'}\n\n"
        "### Compatible Cache Candidates\n\n"
        f"{json.dumps(cache_candidates, ensure_ascii=False, indent=2) if cache_candidates else '(none)'}\n\n"
        "### Cache Protocol\n\n"
        f"{cache_protocol or '(none)'}\n\n"
        "## Required Reading\n\n"
        f"- Project Skill: `{payload.get('project_skill_path')}`\n"
        f"- Site Skill: `{payload.get('site_skill_path')}`\n"
        "- Assistant Context: `workspace/assistant_bridge/context/latest.md`\n\n"
        "## Browser Tool Commands\n\n"
        f"{tool_command_text}\n\n"
        "## CareerEng State Tool Commands\n\n"
        f"{state_tool_text}\n\n"
        "## Contract\n\n"
        "- Use Skills, local memory, evidence, and live browser observations for business/site decisions.\n"
        "- Do not add site-specific workflow or form-filling decisions to Python runtime code.\n"
        "- Use the browser tool commands to observe and operate the existing CareerEng Playwright MCP runtime.\n"
        "- Use the CareerEng state tool commands to persist jobs, application reviews, phase memory, and phase results instead of editing history files directly.\n"
        "- You are the cache decision-maker for this site worker. Follow the cache protocol through cache_lookup, cache_read, cache_propose, and cache_validate; only live evidence decides whether a candidate is reusable.\n"
        "- Propose cache only for cross-thread/backend reusable results, never for private data, raw snapshots, or thread-only reasoning.\n"
        "- If the browser work reveals a workflow gap, create or update an evolution proposal/action card instead of silently continuing with the same failed strategy.\n"
        "- If human-only input is required, leave the browser state resumable and report the exact blocker.\n\n"
        "## Machine Payload\n\n"
        f"- JSON: `{payload.get('payload_path')}`\n"
        f"- Phase Session: `{payload.get('phase_session_path')}`\n"
    )
