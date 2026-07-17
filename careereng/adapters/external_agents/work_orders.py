"""External-agent work order packaging."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

from .contracts import AGENT_BRIDGE_MODE, AGENT_BRIDGE_STATUS
from .state import state_tool_commands
from careereng.orchestration.context import build_phase_context
from careereng.platform.sessions import PhaseSession, write_phase_session
from careereng.orchestration.agent_protocol.state_tools import state_tool_schemas_for_phase
from careereng.utils import ensure_dir, make_id, now_iso, read_json, safe_file_stem, write_json


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

    phase_context = build_phase_context(
        SimpleNamespace(
            slug=str(current_phase_row.get("slug") or ""),
            title=str(current_phase_row.get("title") or ""),
            project_text=str(current_phase_row.get("project_text") or ""),
            site_text=str(current_phase_row.get("site_text") or ""),
        ),
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
        },
    ).as_dict()
    state_commands = state_tool_commands(str(payload.get("site_key") or ""), phase=normalized_phase)
    state_tools = state_tool_schemas_for_phase(normalized_phase, include_phase_result=True)

    payload.update(
        {
            "updated_at": now_iso(),
            "current_phase": normalized_phase,
            "current_phase_context": phase_context,
            "state_tool_commands": state_commands,
            "state_tools": state_tools,
        }
    )
    session_payload.update(
        {
            "current_phase": normalized_phase,
            "phase_context": phase_context,
            "state_tool_commands": state_commands,
            "state_tools": state_tools,
        }
    )
    write_json(payload_path, payload)
    write_json(phase_session_path, session_payload)

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
    phase_context = build_phase_context(
        current_phase_row,
        continuation=continuation_context,
        local_state={
            "site_key": site_key,
            "entry_url": entry_url,
            "batch_id": batch_id,
            "session_id": session_id,
            "turn_id": turn_id,
            "apply_target_job_ids": list(apply_target_job_ids or ()),
        },
    ).as_dict()
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
        "agent_name": str(agent_name or "external_agent"),
        "site_key": site_key,
        "site_name": site_name,
        "entry_url": entry_url,
        "session_id": session_id,
        "turn_id": turn_id,
        "batch_id": batch_id,
        "resume": bool(resume),
        "phase_slugs": list(phase_slugs),
        "current_phase": current_phase,
        "apply_target_job_ids": list(apply_target_job_ids or ()),
        "continuation_context": continuation_context or {},
        "continuation_context_path": continuation_context_path,
        "current_phase_context": phase_context,
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
    legacy_tools = str(tool_commands.get("legacy_tools") or "").strip()
    state_commands = payload.get("state_tool_commands") if isinstance(payload.get("state_tool_commands"), dict) else {}
    tool_command_lines = []
    if preferred_tools:
        tool_command_lines.append(f"- List tools: `{preferred_tools}`")
    if preferred_snapshot:
        tool_command_lines.append(f"- Snapshot: `{preferred_snapshot}`")
    if preferred_call:
        tool_command_lines.append(f"- Call tool: `{preferred_call}`")
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
        "- If the browser work reveals a workflow gap, create or update an evolution proposal/action card instead of silently continuing with the same failed strategy.\n"
        "- If human-only input is required, leave the browser state resumable and report the exact blocker.\n\n"
        "## Machine Payload\n\n"
        f"- JSON: `{payload.get('payload_path')}`\n"
        f"- Phase Session: `{payload.get('phase_session_path')}`\n"
    )
