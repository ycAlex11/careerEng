"""Deprecated compatibility imports for the Runtime Host migration.

Runtime ownership now lives in :mod:`careereng.platform.runtime_host`. Keep this
module importable for existing local integrations while adapters migrate to the
explicit RuntimeHostClient contract.
"""

from careereng.platform.runtime_host.service import (
    DEFAULT_MANAGER_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_RUNTIME_HOST_REQUEST_TIMEOUT_SECONDS,
    RuntimeHostService,
    WorkspaceManager,
    cancel_manager_jobs_batch,
    call_agent_bridge_browser_tool,
    run_agent_bridge_browser_sequence,
    call_agent_bridge_state_tool,
    call_browser_handoff_tool,
    dispatch_manager_message,
    ensure_runtime_host,
    ensure_workspace_manager,
    fresh_snapshot_resume,
    list_agent_bridge_browser_tools,
    list_agent_bridge_state_tools,
    list_browser_handoff_tools,
    manager_socket_path,
    pause_manager_jobs_batch,
    runtime_host_socket_path,
    serve_runtime_host,
    serve_workspace_manager,
    shutdown_runtime_host,
    shutdown_workspace_manager,
    start_manager_jobs_batch,
)
from careereng.platform.runtime_host.client import runtime_host_client


def _legacy_client(*, project_root, workspace):
    """Keep legacy CLI callers working while routing through RuntimeHostClient."""

    return runtime_host_client(project_root=project_root, workspace=workspace, autostart=True)


def dispatch_manager_message(*, project_root, workspace, session_id: str, message: str) -> str:
    response = _legacy_client(project_root=project_root, workspace=workspace).request(
        "process_message",
        {"session_id": session_id, "message": message},
    )
    return str(response.get("reply") or "")


def start_manager_jobs_batch(*, project_root, workspace, session_id: str, message: str, operation: str, apply_requested: bool):
    return _legacy_client(project_root=project_root, workspace=workspace).request(
        "start_jobs_batch",
        {
            "session_id": session_id,
            "message": message,
            "operation": operation,
            "apply_requested": bool(apply_requested),
        },
        timeout=10.0,
    )


def fresh_snapshot_resume(*, project_root, workspace, session_id: str, message: str, turn_id: str = "", site_key: str = ""):
    return _legacy_client(project_root=project_root, workspace=workspace).request(
        "fresh_snapshot_resume",
        {"session_id": session_id, "message": message, "turn_id": turn_id, "site_key": site_key},
    )


def pause_manager_jobs_batch(*, project_root, workspace, batch_id: str, site_key: str = ""):
    return _legacy_client(project_root=project_root, workspace=workspace).request(
        "pause_jobs_batch",
        {"batch_id": batch_id, "site_key": site_key},
    )


def list_agent_bridge_browser_tools(*, project_root, workspace, site_key: str):
    return _legacy_client(project_root=project_root, workspace=workspace).request(
        "agent_bridge_browser_list_tools",
        {"site_key": site_key},
    )


def call_agent_bridge_browser_tool(*, project_root, workspace, site_key: str, tool_name: str, arguments=None, turn_id: str = "", phase: str = "agent_bridge"):
    return _legacy_client(project_root=project_root, workspace=workspace).request(
        "agent_bridge_browser_call_tool",
        {"site_key": site_key, "tool_name": tool_name, "arguments": arguments or {}, "turn_id": turn_id, "phase": phase},
    )


def run_agent_bridge_browser_sequence(*, project_root, workspace, site_key: str, steps: list[dict], turn_id: str = "", phase: str = "agent_bridge"):
    return _legacy_client(project_root=project_root, workspace=workspace).request(
        "agent_bridge_browser_run_sequence",
        {"site_key": site_key, "steps": steps, "turn_id": turn_id, "phase": phase},
    )


def list_agent_bridge_state_tools(*, project_root, workspace, site_key: str, phase: str = ""):
    return _legacy_client(project_root=project_root, workspace=workspace).request(
        "agent_bridge_state_list_tools",
        {"site_key": site_key, "phase": phase},
    )


def call_agent_bridge_state_tool(*, project_root, workspace, site_key: str, tool_name: str, arguments=None, turn_id: str = "", phase: str = ""):
    return _legacy_client(project_root=project_root, workspace=workspace).request(
        "agent_bridge_state_call_tool",
        {"site_key": site_key, "tool_name": tool_name, "arguments": arguments or {}, "turn_id": turn_id, "phase": phase},
    )


def list_browser_handoff_tools(*, project_root, workspace, site_key: str):
    return list_agent_bridge_browser_tools(project_root=project_root, workspace=workspace, site_key=site_key)


def call_browser_handoff_tool(*, project_root, workspace, site_key: str, tool_name: str, arguments=None, turn_id: str = "", phase: str = "agent_bridge"):
    return call_agent_bridge_browser_tool(
        project_root=project_root,
        workspace=workspace,
        site_key=site_key,
        tool_name=tool_name,
        arguments=arguments,
        turn_id=turn_id,
        phase=phase,
    )


def shutdown_workspace_manager(*, project_root, workspace, cancel_open_batches: bool = False, session_id: str | None = None, wait_timeout_seconds: float = 10.0):
    return _legacy_client(project_root=project_root, workspace=workspace).shutdown(
        cancel_open_batches=cancel_open_batches,
        session_id=session_id,
        wait_timeout_seconds=wait_timeout_seconds,
    )

__all__ = [
    "DEFAULT_MANAGER_REQUEST_TIMEOUT_SECONDS",
    "DEFAULT_RUNTIME_HOST_REQUEST_TIMEOUT_SECONDS",
    "RuntimeHostService",
    "WorkspaceManager",
    "cancel_manager_jobs_batch",
    "call_agent_bridge_browser_tool",
    "run_agent_bridge_browser_sequence",
    "call_agent_bridge_state_tool",
    "call_browser_handoff_tool",
    "dispatch_manager_message",
    "ensure_runtime_host",
    "ensure_workspace_manager",
    "fresh_snapshot_resume",
    "list_agent_bridge_browser_tools",
    "list_agent_bridge_state_tools",
    "list_browser_handoff_tools",
    "manager_socket_path",
    "pause_manager_jobs_batch",
    "runtime_host_socket_path",
    "serve_runtime_host",
    "serve_workspace_manager",
    "shutdown_runtime_host",
    "shutdown_workspace_manager",
    "start_manager_jobs_batch",
]
