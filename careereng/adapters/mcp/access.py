"""Authorize Desktop MCP requests from transport-owned task metadata."""

from pathlib import Path
from typing import Any
from contextvars import ContextVar

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import TextContent

from careereng.career.applications.job_store import JobStore
from careereng.orchestration.worker_control.actions import WorkerActionStore
from careereng.orchestration.worker_control.inbox import WorkerCommandInbox
from careereng.orchestration.worker_control.lifecycle import is_terminal_work
from careereng.orchestration.worker_control.registry import NativeWorkerRegistry
from careereng.platform.project_state import AgentEventStore


current_caller: ContextVar[str] = ContextVar("careereng_mcp_caller", default="")


PUBLIC_TOOLS = frozenset({"careereng_ping", "careereng_runtime_host_status", "careereng_get_main_agent_registration"})
WORKER_TOOLS = frozenset({
    "careereng_report_native_worker_state", "careereng_claim_urgent_notification",
    "careereng_record_urgent_notification_send", "careereng_ack_worker_guidance",
    "careereng_work_item_list_browser_tools", "careereng_work_item_call_browser_tool",
    "careereng_work_item_run_browser_sequence", "careereng_work_item_list_state_tools",
    "careereng_work_item_call_state_tool", "careereng_work_item_phase_result",
    "careereng_complete_evolution_solution", "careereng_submit_evolution_proposal",
    "careereng_apply_evolution_solution",
})
SCOPED_READ_TOOLS = frozenset({"careereng_get_work_item_context", "careereng_read_work_item_resource"})
MAIN_TOOLS = frozenset({
    "careereng_list_agent_events", "careereng_wait_agent_events", "careereng_monitor_agent_events",
    "careereng_list_worker_launch_specs", "careereng_register_native_worker",
    "careereng_set_worker_desired_state", "careereng_list_worker_actions",
    "careereng_ack_worker_action", "careereng_prepare_worker_action",
    "careereng_ack_agent_events", "careereng_ack_notifications", "careereng_get_context",
    "careereng_get_batch_status", "careereng_get_agent_status", "careereng_start_jobs_batch",
    "careereng_resume_after_user_action", "careereng_get_worker_command",
    "careereng_send_worker_command", "careereng_pause_jobs_batch", "careereng_pause_site",
    "careereng_stop_site", "careereng_cancel_site", "careereng_set_site_mode",
    "careereng_cancel_jobs_batch", "careereng_release_site",
    "careereng_get_batch_progress",
})


def caller_thread_id(context: Context) -> str:
    try:
        metadata = context.request_context.meta
    except ValueError:
        metadata = None
    value = getattr(metadata, "threadId", None) if metadata is not None else None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("caller_identity_required: Desktop request metadata must include threadId")
    return value.strip()


class DesktopTaskAccess:
    def __init__(self, workspace: Path):
        self.registry = NativeWorkerRegistry(workspace)
        self.events = AgentEventStore(workspace)
        self.jobs = JobStore(workspace)
        self.actions = WorkerActionStore(workspace)
        self.inbox = WorkerCommandInbox(workspace)

    def authorize(self, name: str, arguments: dict[str, Any], caller: str) -> None:
        main = str(self.events.main_agent_registration().get("thread_id") or "")
        if name == "careereng_register_main_agent":
            if arguments.get("thread_id") != caller:
                raise ValueError("caller_identity_mismatch: register only the calling Desktop task")
            active = self.registry.list(active_only=True)
            unsettled = [worker for worker in self.registry.list()
                         if not worker.get("execution_retired") and
                         (worker.get("runtime_state") in {"starting", "running", "quiescing"}
                          or (worker.get("browser_policy") == "release" and worker.get("browser_state") != "absent"))]
            if any(worker.get("agent_id") == caller for worker in active):
                raise ValueError("worker_role_conflict: finish and unbind worker work before becoming supervisor")
            if main and main != caller and (active or unsettled or self.jobs.list_batches(include_terminal=False)):
                raise ValueError("supervisor_takeover_blocked: existing batch or worker requires reconciliation")
            return
        if name in WORKER_TOOLS or name in SCOPED_READ_TOOLS:
            work_item_id = str(arguments.get("work_item_id") or "")
            if name in SCOPED_READ_TOOLS and caller == main:
                worker = self.registry.get(work_item_id=work_item_id)
                if worker:
                    self._require_batch_owner(str(worker.get("batch_id") or ""), caller)
                return
            if caller == main:
                raise ValueError("worker_role_required: the supervisor cannot execute worker operations")
            worker = self.registry.require_binding(work_item_id, agent_id=caller)
            if self.registry.get(agent_id=caller).get("work_item_id") != work_item_id:
                raise ValueError("obsolete_task_binding: the task is assigned to another work item")
            if worker.get("parent_agent_id") != main:
                raise ValueError("worker_parent_mismatch: worker is not owned by the current supervisor")
            self._require_batch_owner(str(worker.get("batch_id") or ""), main)
            return
        if name not in MAIN_TOOLS:
            raise ValueError("tool_access_policy_required: undeclared tool role")
        if not main or caller != main:
            raise ValueError("supervisor_role_required: only the registered main task can control workers")
        batch_id = str(arguments.get("batch_id") or arguments.get("source_batch_id") or "")
        if arguments.get("action_id"):
            batch_id = self.actions.get(str(arguments["action_id"])).batch_id
        elif arguments.get("command_id") and name == "careereng_get_worker_command":
            batch_id = self.inbox.get(str(arguments["command_id"])).batch_id
        elif arguments.get("work_item_id"):
            batch_id = str(self.registry.get(work_item_id=str(arguments["work_item_id"])).get("batch_id") or "")
        if batch_id and batch_id != "latest":
            self._require_batch_owner(batch_id, caller)
        if name == "careereng_register_native_worker":
            if arguments.get("parent_agent_id") not in {None, "", caller}:
                raise ValueError("parent_task_mismatch: worker parent must be the calling supervisor")
            agent_id = str(arguments.get("agent_id") or "")
            if agent_id == caller:
                raise ValueError("worker_role_conflict: supervisor cannot register itself as a worker")
            worker = self.registry.previous_binding(agent_id, excluding=str(arguments.get("work_item_id") or ""))
            if worker:
                if not is_terminal_work(str(worker.get("work_state") or "")) or worker.get("runtime_state") in {"starting", "running", "quiescing"}:
                    raise ValueError("task_reuse_requires_quiescence: previous worker has not stopped")
        if name in {"careereng_send_worker_command", "careereng_release_site", "careereng_resume_after_user_action"}:
            for worker in self.registry.list(active_only=True):
                if worker.get("site_key") == arguments.get("site_key"):
                    self._require_batch_owner(str(worker.get("batch_id") or ""), caller)

    def _require_batch_owner(self, batch_id: str, caller: str) -> None:
        batch = self.jobs.load_batch(batch_id)
        if str(batch.get("parent_agent_id") or "") != caller:
            raise ValueError("batch_owner_mismatch: batch belongs to another supervisor")


class DesktopMCPServer(FastMCP):
    def __init__(self, *args, workspace: Path, **kwargs):
        self.access = DesktopTaskAccess(workspace)
        super().__init__(*args, **kwargs)

    async def call_tool(self, name: str, arguments: dict[str, Any]):
        caller = ""
        if name not in PUBLIC_TOOLS:
            try:
                caller = caller_thread_id(self.get_context())
                self.access.authorize(name, arguments, caller)
            except (ValueError, KeyError) as exc:
                result = {"ok": False, "error": str(exc)}
                return [TextContent(type="text", text=str(exc))], result
        token = current_caller.set(caller)
        try:
            return await super().call_tool(name, arguments)
        finally:
            current_caller.reset(token)
