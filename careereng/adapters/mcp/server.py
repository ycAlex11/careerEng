"""Thin MCP server adapter for CareerEng tools.

The MCP layer is intentionally transport-only: it exposes existing CareerEng
manager and phase-runtime capabilities without adding workflow strategy.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from careereng.adapters.bootstrap import project_root_from_cwd, workspace_path as resolve_workspace_path
from careereng.orchestration.agent_protocol.work_items import build_work_item_context, read_work_item_resource, work_item_id_from_payload
from careereng.orchestration.agent_protocol.work_item_store import WorkItemStore
from careereng.adapters.external_agents.contracts import AGENT_BRIDGE_PROTOCOL_VERSION
from careereng.orchestration.agent_protocol.runtime_lifecycle import release_site_payload
from careereng.career.applications.job_store import JobStore, TERMINAL_BATCH_STATUSES
from careereng.career.applications.site_modes import SITE_MODES
from careereng.career.applications.site_store import SiteStore
from careereng.platform.runtime_host import RUNTIME_HOST_PROTOCOL_VERSION, runtime_host_client, runtime_host_status
from careereng.platform.project_state import AgentEventStore
from careereng.platform.project_state.notifications import AgentNotificationStore
from careereng.platform.project_state.urgent_relay import UrgentNotificationRelay
from careereng.platform.sessions import SiteWorkerSessionStore
from careereng.config.loader import load_config
from careereng.orchestration.worker_control.scheduling import SiteCapacityScheduler, execution_admitted
from careereng.orchestration.worker_control import (
    BrowserResourcePolicy,
    NativeWorkerControlSupervisor,
    NativeWorkerRegistry,
    WorkerActionStore,
    WorkerActionKind,
    WorkerCommandKind,
    WorkerDesiredState,
    WorkerLifecycleReconciler,
    create_worker_action,
    create_worker_command,
    plan_worker_continuity,
)
from careereng.utils import make_id


DEFAULT_SESSION_ID = "cli:default"
DEFAULT_JOBS_MESSAGE = "检索投递已注册的公司"


@dataclass(frozen=True)
class CareerEngMCPRuntime:
    project_root: Path
    workspace: Path

    @classmethod
    def from_paths(cls, *, project_root: Path | None = None, workspace: Path | None = None) -> "CareerEngMCPRuntime":
        root = (project_root or project_root_from_cwd()).expanduser().resolve()
        resolved_workspace = (workspace.expanduser().resolve() if workspace else resolve_workspace_path(root))
        return cls(project_root=root, workspace=resolved_workspace)

    def job_store(self) -> JobStore:
        return JobStore(self.workspace)

    def site_store(self) -> SiteStore:
        return SiteStore(self.workspace, project_root=self.project_root)

    def agent_events(self) -> AgentEventStore:
        return AgentEventStore(self.workspace)

    def native_workers(self) -> NativeWorkerRegistry:
        return NativeWorkerRegistry(self.workspace)

    def worker_actions(self) -> WorkerActionStore:
        return WorkerActionStore(self.workspace)

    def worker_control(self) -> NativeWorkerControlSupervisor:
        return NativeWorkerControlSupervisor(self.workspace)

    def site_scheduler(self) -> SiteCapacityScheduler:
        return SiteCapacityScheduler(self.workspace, limit=load_config(self.project_root).agent.site_parallelism)

    def site_worker_sessions(self) -> SiteWorkerSessionStore:
        return SiteWorkerSessionStore(self.workspace)

    def site_run_threshold(self) -> int:
        return max(1, int(load_config(self.project_root).evolution.batch_review.site_run_threshold or 5))

    def monitor_policy(self) -> dict[str, Any]:
        config = load_config(self.project_root).agent.notifications
        return {
            "progress_interval_seconds": config.progress_interval_seconds,
            "poll_interval_seconds": config.poll_interval_seconds,
            "urgent_bypass_progress_interval": True,
            "delivery_mode": "worker_relay_with_polling_fallback",
            "urgent_relay_tool": "careereng_claim_urgent_notification",
            "urgent_transport_tool": "send_message_to_thread",
            "idle_wakeup_owner": "codex_desktop_heartbeat",
            "worker_task_mode": "visible_desktop_task",
        }

    def host_client(self):
        # The desktop adapter must never create a browser-owning process inside
        # its own sandbox. A user-owned Runtime Host is the only execution owner.
        return runtime_host_client(project_root=self.project_root, workspace=self.workspace, autostart=False)


def _compact_batch(batch: dict[str, Any] | None, *, site_store: SiteStore | None = None) -> dict[str, Any]:
    if not isinstance(batch, dict) or not batch:
        return {}
    sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
    return {
        "batch_id": str(batch.get("batch_id") or ""),
        "session_id": str(batch.get("session_id") or ""),
        "turn_id": str(batch.get("turn_id") or ""),
        "operation": str(batch.get("operation") or ""),
        "execution_backend": str(batch.get("execution_backend") or ""),
        "apply_requested": bool(batch.get("apply_requested")),
        "status": str(batch.get("status") or ""),
        "created_at": str(batch.get("created_at") or ""),
        "updated_at": str(batch.get("updated_at") or ""),
        "site_count": len(sites),
        "sites": {
            str(site_key): _compact_batch_site(
                site,
                browser_session=(site_store.load_browser_session(str(site_key)) if site_store is not None else None),
            )
            for site_key, site in sites.items()
            if isinstance(site, dict) and str(site_key)
        },
    }


def _compact_batch_site(site: dict[str, Any], *, browser_session: dict[str, Any] | None = None) -> dict[str, Any]:
    retrieve = site.get("retrieve") if isinstance(site.get("retrieve"), dict) else {}
    apply = site.get("apply") if isinstance(site.get("apply"), dict) else {}
    browser = browser_session if isinstance(browser_session, dict) else {}
    return {
        "site_key": str(site.get("site_key") or ""),
        "site_name": str(site.get("site_name") or ""),
        "status": str(site.get("status") or ""),
        "reason_tag": str(site.get("reason_tag") or ""),
        "current_phase": str(site.get("current_phase") or ""),
        "current_url": str(site.get("current_url") or ""),
        "retrieve_status": str(retrieve.get("status") or ""),
        "apply_status": str(apply.get("status") or ""),
        "message": str(site.get("message") or "")[:500],
        "worker_status": str(browser.get("codex_worker_status") or ""),
        "worker_last_error": str(browser.get("codex_worker_last_error") or "")[:500],
    }


def _compact_site(site: dict[str, Any], *, browser_session: dict[str, Any] | None = None) -> dict[str, Any]:
    browser = browser_session if isinstance(browser_session, dict) else {}
    return {
        "site_key": str(site.get("site_key") or site.get("site_id") or ""),
        "site_name": str(site.get("canonical_company") or site.get("raw_name") or ""),
        "status": str(site.get("status") or ""),
        "base_url": str(site.get("base_url") or ""),
        "browser_status": str(browser.get("browser_status") or ""),
        "pending_action": str(browser.get("pending_action") or ""),
        "resume_phase": str(browser.get("resume_phase") or ""),
        "last_known_url": str(browser.get("last_known_url") or ""),
        "current_trace_ref": str(browser.get("current_trace_ref") or ""),
    }


def _latest_batch(store: JobStore, *, session_id: str, batch_id: str) -> dict[str, Any] | None:
    requested = str(batch_id or "").strip()
    if requested and requested != "latest":
        return store.load_batch(requested)
    return store.latest_open_batch(session_id) or (store.list_batches(session_id=session_id)[:1] or [None])[0]


def _active_work_item_payload(runtime: CareerEngMCPRuntime, work_item_id: str) -> dict[str, Any]:
    """Resolve an active persisted work item without exposing its file path."""

    return WorkItemStore(runtime.workspace).resolve_active(work_item_id)


def _active_work_item_scope(
    runtime: CareerEngMCPRuntime,
    work_item_id: str,
    *,
    expected_context_revision: int | None = None,
    expected_apply_target_job_id: str = "",
) -> dict[str, Any]:
    """Resolve the immutable execution scope for one active worker item."""

    payload = _active_work_item_payload(runtime, work_item_id)
    context = build_work_item_context(payload)
    scope = context.get("scope") if isinstance(context.get("scope"), dict) else {}
    site_key = str(scope.get("site_key") or "").strip()
    batch_id = str(scope.get("batch_id") or "").strip()
    phase = str((context.get("objective") or {}).get("phase") or "").strip()
    evolution_run_id = str(scope.get("evolution_run_id") or "").strip()
    if not site_key or not batch_id or not phase:
        raise ValueError("active work item has incomplete execution scope")
    batch = runtime.job_store().load_batch(batch_id)
    if str(batch.get("status") or "") in TERMINAL_BATCH_STATUSES and not evolution_run_id:
        raise ValueError("work item batch is terminal")
    sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
    site = sites.get(site_key) if isinstance(sites.get(site_key), dict) else {}
    if not site:
        raise ValueError("work item site is not active in its batch")
    context_revision = int(payload.get("context_revision") or 0)
    if expected_context_revision is not None and context_revision != int(expected_context_revision):
        raise ValueError(
            "work item context revision is stale "
            f"(expected={expected_context_revision}, current={context_revision})"
        )
    apply_target_job_ids = [str(value or "").strip() for value in scope.get("apply_target_job_ids") or [] if str(value or "").strip()]
    expected_target = str(expected_apply_target_job_id or "").strip()
    if expected_target and expected_target not in apply_target_job_ids:
        raise ValueError("apply target fence does not match the active work item target")
    return {
        "work_item_id": str(context.get("work_item_id") or ""),
        "site_key": site_key,
        "batch_id": batch_id,
        "phase": phase,
        "turn_id": str(scope.get("turn_id") or ""),
        "evolution_run_id": evolution_run_id,
        "context_revision": context_revision,
        "apply_target_job_ids": apply_target_job_ids,
        "control_epoch": int(payload.get("control_epoch") or 0),
        "site_revision": int(payload.get("site_revision") or 0),
    }


def _work_item_fence_payload(scope: dict[str, Any]) -> dict[str, Any]:
    return {
        "work_item_id": scope["work_item_id"],
        "batch_id": scope["batch_id"],
        "context_revision": scope["context_revision"],
        "control_epoch": scope["control_epoch"],
        "site_revision": scope["site_revision"],
    }


def create_mcp_server(*, project_root: Path | None = None, workspace: Path | None = None) -> FastMCP:
    runtime = CareerEngMCPRuntime.from_paths(project_root=project_root, workspace=workspace)
    server = FastMCP(
        "careereng",
        instructions=(
            "CareerEng MCP tools expose local CareerEng workflow capabilities to Codex. "
            "Top-level tools are monitoring and lifecycle controls only. Browser and state "
            "execution is allowed only through careereng_work_item_* tools bound to one active "
            "worker item. Business judgment must stay in Skills, memory, evolution proposals, "
            "and the LLM. The Codex Desktop main Agent is the only native worker supervisor: "
            "it creates flat workers, executes CareerEng action plans with native Agent tools, "
            "and writes receipts back to CareerEng. Workers never create or control other workers."
        ),
    )

    def plan_worker_state(*, worker: dict[str, Any], desired_state: str, browser_policy: str) -> list[dict[str, Any]]:
        normalized = WorkerDesiredState(str(desired_state))
        updated = runtime.native_workers().update(
            worker["work_item_id"],
            desired_state=normalized.value,
            browser_policy=BrowserResourcePolicy(str(browser_policy)).value,
        )
        if normalized == WorkerDesiredState.RUNNING:
            updated = runtime.site_scheduler().resume(worker["work_item_id"])
        runtime.site_scheduler().reconcile()
        command_kind = {
            WorkerDesiredState.RUNNING: WorkerCommandKind.RESUME,
            WorkerDesiredState.PAUSED: WorkerCommandKind.PAUSE,
            WorkerDesiredState.CANCELLED: WorkerCommandKind.CANCEL,
        }.get(normalized)
        if command_kind is None:
            actions = WorkerLifecycleReconciler().plan(updated, resource_policy=browser_policy)
            return [runtime.worker_actions().enqueue(action).as_dict() for action in actions]
        command = create_worker_command(
            command_id=(
                f"worker_command:{updated['work_item_id']}:{updated.get('control_epoch', 0)}:"
                f"{normalized.value}:{browser_policy}"
            ),
            site_key=str(updated.get("site_key") or ""),
            batch_id=str(updated.get("batch_id") or ""),
            work_item_id=str(updated.get("work_item_id") or ""),
            kind=command_kind,
            expected_control_epoch=int(updated.get("control_epoch") or 0),
            message=str((updated.get("launch_spec") or {}).get("prompt") or "") if command_kind == WorkerCommandKind.RESUME else "",
        )
        _, actions = runtime.worker_control().enqueue(command)
        return [action.as_dict() for action in actions]

    def workers_for_scope(*, batch_id: str, site_key: str = "") -> list[dict[str, Any]]:
        records = {
            str(row.get("work_item_id") or ""): row
            for row in WorkItemStore(runtime.workspace).list_records(batch_id=batch_id)
        }
        workers = []
        for row in runtime.native_workers().list(batch_id=batch_id):
            if site_key and str(row.get("site_key") or "") != site_key:
                continue
            agent_id = str(row.get("agent_id") or "")
            latest = runtime.native_workers().get(agent_id=agent_id) if agent_id else row
            if latest.get("work_item_id") != row.get("work_item_id"):
                continue
            if row.get("work_state") in {"completed", "cancelled", "failed"} and row.get("runtime_state") in {"terminal", "detached"}:
                continue
            durable = records.get(str(row.get("work_item_id") or ""), {})
            if durable:
                terminal_state = str(durable.get("state") or "")
                row = runtime.native_workers().update(
                    str(row.get("work_item_id") or ""),
                    control_epoch=int(durable.get("control_epoch") or 0),
                    **({"work_state": terminal_state} if terminal_state in {"completed", "cancelled"} else {}),
                )
            workers.append(row)
        return workers

    def reconcile_worker_liveness() -> list[dict[str, Any]]:
        runtime.site_scheduler().reconcile()
        recovery = load_config(runtime.project_root).agent.recovery
        results = runtime.worker_control().reconcile_liveness(
            idle_timeout_seconds=int(recovery.idle_timeout_seconds),
            max_resume_attempts=int(recovery.max_resume_attempts),
            interrupt_ack_timeout_seconds=int(recovery.interrupt_ack_timeout_seconds),
            max_interrupt_attempts=int(recovery.max_interrupt_attempts),
            probe_interval_seconds=int(recovery.probe_interval_seconds),
            failure_threshold=int(recovery.failure_threshold),
            inflight_timeout_seconds=int(recovery.inflight_timeout_seconds),
        )
        serialized = []
        for result in results:
            worker = dict(result.get("worker") or {})
            actions = [row.as_dict() for row in result.get("actions") or []]
            kind = str(result.get("kind") or "worker_liveness")
            attention = "action_required" if kind in {"interrupt_unconfirmed", "recovery_exhausted"} else "notification"
            event = runtime.agent_events().publish(
                kind=f"worker.{kind}",
                attention=attention,
                summary=f"Worker liveness reconciliation reported {kind} for {worker.get('site_key') or worker.get('work_item_id')}.",
                site_key=str(worker.get("site_key") or ""),
                batch_id=str(worker.get("batch_id") or ""),
                details={
                    "work_item_id": str(worker.get("work_item_id") or ""),
                    "agent_id": str(worker.get("agent_id") or ""),
                    "action_ids": [row["action_id"] for row in actions],
                },
                dedupe_key=f"worker_liveness:{kind}:{worker.get('work_item_id')}:{worker.get('revision')}",
            )
            serialized.append({"kind": kind, "worker": worker, "actions": actions, "event": event})
        return serialized

    @server.tool()
    def careereng_ping() -> dict[str, Any]:
        """Check that the CareerEng MCP server is reachable."""
        return {
            "ok": True,
            "bridge_protocol_version": AGENT_BRIDGE_PROTOCOL_VERSION,
            "runtime_host_protocol_version": RUNTIME_HOST_PROTOCOL_VERSION,
            "project_root": str(runtime.project_root),
            "workspace": str(runtime.workspace),
        }

    @server.tool()
    def careereng_runtime_host_status() -> dict[str, Any]:
        """Check whether the user-owned local runtime host is reachable."""
        return runtime_host_status(project_root=runtime.project_root, workspace=runtime.workspace)

    @server.tool()
    def careereng_list_agent_events(
        cursor: str = "",
        site_key: str = "",
        batch_id: str = "",
        include_notifications: bool = True,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Read new durable events for the Codex Desktop main-agent inbox."""
        return {
            "ok": True,
            **runtime.agent_events().list_events(
                consumer_id="codex_desktop",
                cursor=cursor,
                site_key=site_key,
                batch_id=batch_id,
                include_notifications=include_notifications,
                limit=limit,
            ),
        }

    @server.tool()
    async def careereng_wait_agent_events(
        cursor: str = "",
        site_key: str = "",
        batch_id: str = "",
        include_notifications: bool = True,
        limit: int = 100,
        timeout_seconds: float = 5.0,
    ) -> dict[str, Any]:
        """Cancellably wait for durable events while the current main-Agent turn is active."""
        reconcile_worker_liveness()
        bounded_timeout = min(5.0, max(0.0, float(timeout_seconds or 0.0)))
        event_store = runtime.agent_events()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + bounded_timeout
        while True:
            policy = runtime.monitor_policy()
            notifications = AgentNotificationStore(runtime.workspace).plan(
                progress_interval_seconds=policy["progress_interval_seconds"], site_key=site_key, batch_id=batch_id,
            )
            listed = event_store.list_events(
                consumer_id="codex_desktop",
                cursor=cursor,
                site_key=site_key,
                batch_id=batch_id,
                include_notifications=include_notifications,
                limit=limit,
            )
            if listed["events"] or notifications:
                return {"ok": True, "max_wait_seconds": 5.0, "timed_out": False, **listed,
                        "notifications": notifications, "monitor_policy": policy}
            remaining = deadline - loop.time()
            if remaining <= 0:
                return {"ok": True, "max_wait_seconds": 5.0, "timed_out": True, **listed,
                        "notifications": [], "monitor_policy": policy}
            await asyncio.sleep(min(0.25, remaining))

    @server.tool()
    async def careereng_monitor_agent_events(
        cursor: str = "",
        site_key: str = "",
        batch_id: str = "",
        include_notifications: bool = True,
        limit: int = 100,
        timeout_seconds: float = 5.0,
    ) -> dict[str, Any]:
        """Stable bounded monitor alias for Desktop supervisors."""
        return await careereng_wait_agent_events(
            cursor=cursor,
            site_key=site_key,
            batch_id=batch_id,
            include_notifications=include_notifications,
            limit=limit,
            timeout_seconds=timeout_seconds,
        )

    @server.tool()
    def careereng_list_worker_launch_specs(batch_id: str) -> dict[str, Any]:
        """Return ordered actions for active work items and bounded child-task continuity."""
        records = WorkItemStore(runtime.workspace).list_records(batch_id=batch_id, states={"active"})
        specs = []
        actions = []
        for record in records:
            work_item_id = str(record.get("work_item_id") or "")
            existing = runtime.native_workers().get(work_item_id=work_item_id)
            if existing.get("launch_spec"):
                if not existing.get("agent_id"):
                    specs.append(existing["launch_spec"])
                continue
            if not work_item_id or str(existing.get("agent_id") or ""):
                continue
            payload = _active_work_item_payload(runtime, work_item_id)
            context = build_work_item_context(payload)
            scope = context.get("scope") if isinstance(context.get("scope"), dict) else {}
            worker_kind = "evolution" if str(scope.get("evolution_run_id") or "") else "site"
            session_binding = None
            continuity = None
            previous_worker: dict[str, Any] = {}
            if worker_kind == "site":
                session_binding = runtime.site_worker_sessions().bind_batch(
                    site_key=str(record.get("site_key") or ""),
                    backend="native_agent",
                    batch_id=str(record.get("batch_id") or ""),
                    max_effective_batches=runtime.site_run_threshold(),
                )
                previous_worker = runtime.native_workers().latest_for_session(session_binding.worker_session_id)
                continuity = plan_worker_continuity(
                    bound_agent_id=session_binding.thread_id,
                    previous_worker=previous_worker,
                )
                if continuity.replacement_reason and session_binding.thread_id:
                    runtime.site_worker_sessions().quarantine_thread(
                        worker_session_id=session_binding.worker_session_id,
                        thread_id=session_binding.thread_id,
                        reason=continuity.replacement_reason,
                    )
            spec = {
                "work_item_id": work_item_id,
                "site_key": str(record.get("site_key") or ""),
                "batch_id": str(record.get("batch_id") or ""),
                "worker_kind": worker_kind,
                "worker_session_id": session_binding.worker_session_id if session_binding else "",
                "continuity_mode": continuity.mode.value if continuity else "spawn",
                "control_epoch": int(record.get("control_epoch") or 0),
                "desktop_task": {"creation_tool": "create_thread", "visible": True,
                                 "title": f"CareerEng {str(record.get('site_key') or '').title()} Worker",
                                 "reuse_tool": "send_message_to_thread", "forbidden_tool": "spawn_agent"},
                "prompt": (
                    "You are a flat CareerEng worker owned by the Codex Desktop main Agent. "
                    f"Call careereng_get_work_item_context with work_item_id={work_item_id}, "
                    "follow its scoped capabilities and Skills, report every durable phase result through CareerEng, "
                    "and report native state using only the declared enum values. While a turn is executing use "
                    "runtime_state=running; after this work item finishes but this task remains reusable use "
                    "runtime_state=suspended and work_state=completed; reserve runtime_state=terminal for an "
                    "inaccessible or permanently closed task. "
                    "Follow the shared jobs Skill's Waiting And Capacity Policy. After recording a waiting phase, "
                    "report native waiting_user state with wait_decision containing slot_policy (retain/release), "
                    "reason, evidence and resume_condition. Release only after finishing browser operations; "
                    "report runtime_state=suspended. Waiting does not implicitly release capacity. "
                    "Do not create or manage other agents. After reporting waiting_user, failure, "
                    "or completion, call careereng_claim_urgent_notification with this work item "
                    "and its current control epoch. If send_required, call Desktop "
                    "send_message_to_thread with the returned target/message, then record the "
                    "transport result using careereng_record_urgent_notification_send. "
                    "Do not acknowledge user presentation yourself or bypass CareerEng. "
                    "If unavailable or failed, leave events pending for polling; do not busy-loop."
                ),
            }
            specs.append(spec)
            worker = runtime.native_workers().plan(
                work_item_id=work_item_id,
                site_key=spec["site_key"],
                batch_id=spec["batch_id"],
                worker_kind=worker_kind,
                control_epoch=spec["control_epoch"],
                worker_session_id=spec["worker_session_id"],
                agent_id=continuity.agent_id if continuity else "",
                runtime_state=(
                    str(previous_worker.get("runtime_state") or "running")
                    if continuity and continuity.agent_id
                    else "detached"
                ),
            )
            worker = runtime.site_scheduler().enroll(work_item_id)
            runtime.native_workers().update(work_item_id, launch_spec=spec)
            planned_kinds = continuity.action_kinds if continuity else (WorkerActionKind.SPAWN,)
            prerequisite_action_id = ""
            for kind in planned_kinds:
                action_payload = {
                    "desktop_task": spec["desktop_task"],
                    "worker_kind": worker_kind,
                    "worker_session_id": spec["worker_session_id"],
                    "worker_revision": worker["revision"],
                }
                if kind == WorkerActionKind.SPAWN:
                    action_payload["prompt"] = spec["prompt"]
                elif kind == WorkerActionKind.SEND:
                    action_payload["message"] = spec["prompt"]
                    if prerequisite_action_id:
                        action_payload["depends_on_action_id"] = prerequisite_action_id
                else:
                    action_payload["reason"] = "cross_batch_continuity"
                persisted = runtime.worker_actions().enqueue(
                    create_worker_action(
                        kind=kind,
                        agent_id=str(worker.get("agent_id") or ""),
                        work_item_id=work_item_id,
                        site_key=spec["site_key"],
                        batch_id=spec["batch_id"],
                        control_epoch=spec["control_epoch"],
                        payload=action_payload,
                    )
                )
                prerequisite_action_id = persisted.action_id
        scheduling = runtime.site_scheduler().reconcile()
        specs = [spec for spec in specs if execution_admitted(runtime.native_workers().get(work_item_id=spec["work_item_id"]))]
        actions = [row.as_dict() for row in runtime.worker_actions().pending(batch_id=batch_id)]
        return {"ok": True, "batch_id": batch_id, "launch_specs": specs, "actions": actions,
                "scheduling": scheduling,
                "monitor_policy": runtime.monitor_policy()}

    @server.tool()
    def careereng_register_native_worker(
        agent_id: str,
        work_item_id: str,
        batch_id: str,
        site_key: str = "",
        worker_kind: str = "site",
        parent_agent_id: str = "",
        control_epoch: int = 0,
        worker_session_id: str = "",
    ) -> dict[str, Any]:
        """Bind one Desktop-native flat worker to its durable CareerEng work item."""
        record = WorkItemStore(runtime.workspace).list_records(batch_id=batch_id)
        scope = next((row for row in record if str(row.get("work_item_id") or "") == work_item_id), None)
        if scope is None:
            return {"ok": False, "error": "work item does not belong to the requested batch"}
        resolved_site = str(scope.get("site_key") or "")
        if site_key and site_key != resolved_site:
            return {"ok": False, "error": "site key does not match the durable work item"}
        try:
            planned = runtime.native_workers().get(work_item_id=work_item_id)
            if not planned or planned.get("worker_kind") != worker_kind:
                raise ValueError("worker kind must match the CareerEng launch plan")
            if worker_kind == "site" and not planned.get("slot_state"):
                raise ValueError("obtain admitted worker launch specs before registration")
            if not execution_admitted(planned):
                raise ValueError("worker has not been admitted by the capacity scheduler")
            resolved_session_id = str(worker_session_id or planned.get("worker_session_id") or "")
            if worker_kind == "site" and not resolved_session_id:
                resolved_session_id = runtime.site_worker_sessions().bind_batch(
                    site_key=resolved_site,
                    backend="native_agent",
                    batch_id=batch_id,
                    max_effective_batches=runtime.site_run_threshold(),
                ).worker_session_id
            worker = runtime.native_workers().register(
                agent_id=agent_id, work_item_id=work_item_id, site_key=resolved_site,
                batch_id=batch_id, worker_kind=worker_kind, parent_agent_id=parent_agent_id,
                control_epoch=control_epoch or int(scope.get("control_epoch") or 0),
                worker_session_id=resolved_session_id,
            )
            if worker_kind == "site" and resolved_session_id:
                runtime.site_worker_sessions().bind_thread(
                    worker_session_id=resolved_session_id,
                    thread_id=agent_id,
                    reason="native_agent_registered",
                )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "worker": worker}

    @server.tool()
    def careereng_report_native_worker_state(
        work_item_id: str,
        runtime_state: Literal["detached", "starting", "running", "quiescing", "suspended", "terminal", "faulted"],
        work_state: Literal["queued", "running", "waiting_user", "paused", "completed", "failed", "cancelled"],
        expected_control_epoch: int,
        browser_state: Literal["absent", "starting", "ready", "retained", "releasing", "lost"] | None = None,
        error: str = "",
        heartbeat: bool = True,
        wait_decision: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Record observed worker state without interpreting site behavior."""
        changes: dict[str, Any] = {
            "runtime_state": runtime_state, "work_state": work_state,
            "last_error": str(error or ""), "heartbeat": bool(heartbeat),
        }
        changes["expected_control_epoch"] = expected_control_epoch
        if heartbeat and runtime_state == "running":
            changes["recovery_attempts"] = 0
        if runtime_state in {"suspended", "terminal", "faulted"}:
            changes["interrupt_ack_started_at"] = ""
            changes["control_state"] = (
                "waiting_user"
                if work_state == "waiting_user"
                else "paused"
                if runtime_state == "suspended"
                else "stopped"
            )
        if browser_state is not None:
            changes["browser_state"] = browser_state
        try:
            worker = runtime.site_scheduler().report(work_item_id, changes=changes, decision=wait_decision)
            scheduling = runtime.site_scheduler().reconcile()
            worker = runtime.native_workers().get(work_item_id=work_item_id)
        except (KeyError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        policy = str(worker.get("browser_policy") or BrowserResourcePolicy.UNCHANGED.value)
        work_state = str(worker.get("work_state") or work_state)
        actions = [
            runtime.worker_actions().enqueue(action).as_dict()
            for action in WorkerLifecycleReconciler().plan(worker, resource_policy=policy)
        ]
        actions.extend(action.as_dict() for action in runtime.worker_control().reconcile(work_item_id))
        attention = "action_required" if work_state == "waiting_user" else "notification"
        event = runtime.agent_events().publish(
            kind=f"worker.{work_state}", attention=attention,
            summary=f"{worker.get('site_key') or worker.get('worker_kind')} worker is {work_state}.",
            site_key=str(worker.get("site_key") or ""), batch_id=str(worker.get("batch_id") or ""),
            details={"work_item_id": work_item_id, "agent_id": str(worker.get("agent_id") or ""), "error": error,
                     "wait_decision": dict(worker.get("wait_decision") or {})},
            dedupe_key=f"native-worker:{work_item_id}:{worker.get('revision')}:{work_state}",
        )
        return {"ok": True, "worker": worker, "event": event, "actions": actions,
                "scheduling": scheduling,
                "notification_policy": runtime.monitor_policy()}

    @server.tool()
    def careereng_claim_urgent_notification(work_item_id: str, expected_control_epoch: int) -> dict[str, Any]:
        """Claim a durable urgent signal; the worker sends it with Desktop send_message_to_thread."""
        registry = runtime.native_workers()
        try:
            with registry._lock:
                worker = registry.get(work_item_id=work_item_id)
                if not worker:
                    raise ValueError("worker binding required")
                return {"ok": True, **UrgentNotificationRelay(runtime.workspace).claim(
                    worker=worker, expected_control_epoch=expected_control_epoch,
                    progress_interval_seconds=runtime.monitor_policy()["progress_interval_seconds"],
                )}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def careereng_record_urgent_notification_send(
        work_item_id: str, attempt_id: str, accepted: bool, error: str = "",
    ) -> dict[str, Any]:
        """Record transport acceptance or failure, never user presentation or worker liveness."""
        try:
            return {"ok": True, **UrgentNotificationRelay(runtime.workspace).receipt(
                work_item_id=work_item_id, attempt_id=attempt_id, accepted=accepted, error=error,
            )}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def careereng_set_worker_desired_state(
        work_item_id: str,
        desired_state: Literal["running", "paused", "cancelled", "completed"] | str,
        browser_policy: Literal["unchanged", "retain", "release", "restore"] | str = "unchanged",
    ) -> dict[str, Any]:
        """Set desired lifecycle state and materialize supervisor-executable actions."""
        try:
            normalized = WorkerDesiredState(str(desired_state))
            policy = BrowserResourcePolicy(str(browser_policy))
            worker = runtime.native_workers().get(work_item_id=work_item_id)
            if not worker:
                raise KeyError(f"native worker not found: {work_item_id}")
            if normalized == WorkerDesiredState.RUNNING and worker.get("slot_state") and worker.get("work_state") in {"waiting_user", "paused"}:
                raise ValueError("use careereng_resume_after_user_action to resume with a new work-item lease")
            persisted = plan_worker_state(
                worker=worker,
                desired_state=normalized.value,
                browser_policy=policy.value,
            )
            worker = runtime.native_workers().get(work_item_id=work_item_id)
        except (KeyError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "worker": worker, "actions": persisted}

    @server.tool()
    def careereng_list_worker_actions(batch_id: str = "", site_key: str = "") -> dict[str, Any]:
        """List pending native actions for execution by the main-Agent supervisor."""
        scheduling = runtime.site_scheduler().reconcile()
        return {"ok": True, "scheduling": scheduling, "actions": [row.as_dict() for row in runtime.worker_actions().pending(batch_id=batch_id, site_key=site_key)]}

    @server.tool()
    def careereng_ack_worker_action(action_id: str, applied: bool, error: str = "") -> dict[str, Any]:
        """Persist the receipt for one native action executed by the main Agent."""
        try:
            action, command = runtime.worker_control().acknowledge(action_id, applied=applied, error=error)
        except (KeyError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "action": action.as_dict(), "command": command.as_dict() if command else {}}

    @server.tool()
    def careereng_prepare_worker_action(action_id: str) -> dict[str, Any]:
        """Revalidate and claim an action immediately before Desktop execution."""
        try:
            runtime.site_scheduler().reconcile()
            action = runtime.worker_control().prepare_action(action_id)
        except (KeyError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "action": action.as_dict()}

    @server.tool()
    def careereng_ack_agent_events(cursor: str) -> dict[str, Any]:
        """Acknowledge agent events through a durable Desktop cursor."""
        try:
            AgentNotificationStore(runtime.workspace).plan(
                progress_interval_seconds=runtime.monitor_policy()["progress_interval_seconds"],
            )
            return {"ok": True, **runtime.agent_events().acknowledge(consumer_id="codex_desktop", cursor=cursor)}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def careereng_ack_notifications(delivery_id: str) -> dict[str, Any]:
        """Acknowledge a notification only after presenting it to the user."""
        try:
            return {"ok": True, **AgentNotificationStore(runtime.workspace).acknowledge(delivery_id)}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def careereng_register_main_agent(thread_id: str, allow_takeover: bool = False) -> dict[str, Any]:
        """Register this Codex App Server thread as the workspace main agent."""
        try:
            registration = runtime.agent_events().register_main_agent(
                thread_id=thread_id,
                allow_takeover=allow_takeover,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **registration}

    @server.tool()
    def careereng_get_main_agent_registration() -> dict[str, Any]:
        """Return the current workspace main-agent callback target."""
        event_store = runtime.agent_events()
        pending = event_store.list_events(
            consumer_id=str(event_store.main_agent_registration().get("consumer_id") or "codex_desktop"),
            limit=100,
        )
        return {
            "ok": True,
            "registration": event_store.main_agent_registration(),
            "inbox_health": {
                "pending_count": len(pending.get("events") or []),
                "has_attention_required": bool(pending.get("has_attention_required")),
                "next_cursor": str(pending.get("next_cursor") or ""),
            },
        }

    @server.tool()
    def careereng_get_agent_status(site_key: str = "") -> dict[str, Any]:
        """Return current host-owned execution state grouped by site, not batch."""
        return runtime.host_client().agent_status(site_key=site_key)

    @server.tool()
    def careereng_get_context(
        session_id: str = DEFAULT_SESSION_ID,
        batch_id: str = "latest",
        site_key: str = "",
    ) -> dict[str, Any]:
        """Return compact monitoring context without executable phase instructions."""
        job_store = runtime.job_store()
        site_store = runtime.site_store()
        batch = _latest_batch(job_store, session_id=session_id, batch_id=batch_id)
        sites = site_store.list_sites(status="active")
        if site_key:
            sites = [site for site in sites if str(site.get("site_key") or site.get("site_id") or "") == site_key]
        compact_sites = []
        for site in sites:
            key = str(site.get("site_key") or site.get("site_id") or "")
            try:
                browser_session = site_store.load_browser_session(key) if key else {}
            except Exception:
                browser_session = {}
            compact_sites.append(_compact_site(site, browser_session=browser_session))
        return {
            "ok": True,
            "bridge_protocol_version": AGENT_BRIDGE_PROTOCOL_VERSION,
            "runtime_host_protocol_version": RUNTIME_HOST_PROTOCOL_VERSION,
            "project_root": str(runtime.project_root),
            "workspace": str(runtime.workspace),
            "session_id": session_id,
            "batch": _compact_batch(batch, site_store=site_store),
            "active_sites": compact_sites,
            "monitor_policy": runtime.monitor_policy(),
        }

    @server.tool()
    def careereng_get_work_item_context(work_item_id: str) -> dict[str, Any]:
        """Return a bounded work-item scope, context catalog, and MCP capabilities."""
        try:
            payload = _active_work_item_payload(runtime, work_item_id)
            context = build_work_item_context(payload)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        from careereng.platform.observability import PerformanceRecorder

        PerformanceRecorder(runtime.workspace).record(
            backend="external_agent",
            operation="work_item_context",
            site_key=str(context.get("scope", {}).get("site_key") or ""),
            batch_id=str(context.get("scope", {}).get("batch_id") or ""),
            phase=str(context.get("objective", {}).get("phase") or ""),
            status="ok",
            context_catalog_size=len(context.get("context_catalog") or []),
        )
        worker = runtime.native_workers().get(work_item_id=work_item_id)
        return {"ok": True, **context, "notification_policy": runtime.monitor_policy(),
                "scheduling": {"slot_state": worker.get("slot_state", ""),
                               "execution_admitted": execution_admitted(worker),
                               "wait_decision": worker.get("wait_decision", {})}}

    @server.tool()
    def careereng_read_work_item_resource(
        work_item_id: str,
        resource_id: str,
        offset: int = 0,
        limit: int = 8000,
    ) -> dict[str, Any]:
        """Read a selected scoped resource, optionally in a bounded text slice."""
        try:
            payload = _active_work_item_payload(runtime, work_item_id)
            context = build_work_item_context(payload)
            requested = str(resource_id or "").strip()
            catalog_ids = {
                str(row.get("resource_id") or "")
                for row in context.get("context_catalog") or []
                if isinstance(row, dict)
            }
            if requested not in catalog_ids:
                raise ValueError(f"work-item resource is not available: {requested or '<missing>'}")
            if requested == "execution_diagnostics":
                scope = _active_work_item_scope(runtime, work_item_id)
                from careereng.platform.observability import ExecutionDiagnosticStore

                resource = {
                    "work_item_id": scope["work_item_id"],
                    "resource_id": requested,
                    "value": ExecutionDiagnosticStore(runtime.workspace).latest(
                        site_key=scope["site_key"],
                        batch_id=scope["batch_id"],
                    ),
                }
            elif requested in {"apply_facts", "full_cv", "full_persona", "history_view"}:
                scope = _active_work_item_scope(runtime, work_item_id)
                response = runtime.host_client().request(
                    "agent_bridge_read_context_resource",
                    {
                        "site_key": scope["site_key"],
                        "resource_id": requested,
                        "phase": scope["phase"],
                    },
                )
                if not response.get("ok"):
                    return {"ok": False, "error": str(response.get("error") or "context resource unavailable")}
                resource_result = response.get("result") if isinstance(response.get("result"), dict) else {}
                value = resource_result.get("content") if isinstance(resource_result.get("content"), list) else resource_result
                resource = {
                    "work_item_id": scope["work_item_id"],
                    "resource_id": requested,
                    "value": value,
                    "result": resource_result,
                }
            else:
                resource = read_work_item_resource(payload, requested, offset=offset, limit=limit)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        from careereng.platform.observability import PerformanceRecorder

        value = resource.get("value")
        PerformanceRecorder(runtime.workspace).record(
            backend="external_agent",
            operation="work_item_resource_read",
            site_key=str(context.get("scope", {}).get("site_key") or ""),
            batch_id=str(context.get("scope", {}).get("batch_id") or ""),
            phase=str(context.get("objective", {}).get("phase") or ""),
            status="ok",
            resource_id=str(resource.get("resource_id") or ""),
            resource_bytes=len(str(value).encode("utf-8")),
        )
        return {"ok": True, **resource}

    @server.tool()
    def careereng_get_batch_status(
        batch_id: str = "latest",
        session_id: str = DEFAULT_SESSION_ID,
    ) -> dict[str, Any]:
        """Return compact status for one batch, or the latest open batch by default."""
        batch = _latest_batch(runtime.job_store(), session_id=session_id, batch_id=batch_id)
        return {"ok": True, "batch": _compact_batch(batch, site_store=runtime.site_store())}

    @server.tool()
    def careereng_start_jobs_batch(
        message: str = DEFAULT_JOBS_MESSAGE,
        operation: str = "job_search",
        apply_requested: bool = True,
        session_id: str = DEFAULT_SESSION_ID,
        backend: Literal["provider", "codex"] | str = "",
        separate_batch: bool = False,
    ) -> dict[str, Any]:
        """Start a jobs batch on the explicitly selected configured backend."""
        result = runtime.host_client().request(
            "start_jobs_batch",
            {
                "session_id": session_id,
                "message": message,
                "operation": operation,
                "apply_requested": bool(apply_requested),
                "backend": str(backend or ""),
                "separate_batch": bool(separate_batch),
            },
            timeout=10.0,
        )
        if not bool(result.get("accepted")):
            return result
        return {
            **result,
            "supervisor_next": {
                "monitor_tool": "careereng_monitor_agent_events",
                "launch_tool": "careereng_list_worker_launch_specs",
                "batch_id": str(result.get("batch_id") or ""),
            },
        }

    @server.tool()
    def careereng_resume_after_user_action(
        site_key: str,
        message: str = "",
        session_id: str = DEFAULT_SESSION_ID,
        command_id: str = "",
        source_batch_id: str = "",
    ) -> dict[str, Any]:
        """Continue in place, or recover from a terminal batch checkpoint."""
        resume_message = str(message or "").strip() or f"{site_key} done"
        effective_command_id = str(command_id or make_id("worker_command"))
        work_items = WorkItemStore(runtime.workspace)
        result = runtime.host_client().request(
            "fresh_snapshot_resume",
            {
                "session_id": session_id,
                "message": resume_message,
                "turn_id": make_id("turn"),
                "site_key": site_key,
                "command_id": effective_command_id,
                "source_batch_id": str(source_batch_id or ""),
            },
        )
        if not bool(result.get("accepted")):
            return result
        actions = []
        resolved_batch_id = str(result.get("batch_id") or "")
        active_records = {
            str(record.get("work_item_id") or ""): record
            for record in work_items.list_records(batch_id=resolved_batch_id, states={"active"})
        } if resolved_batch_id else {}
        for worker in runtime.native_workers().list(batch_id=resolved_batch_id, active_only=True):
            if str(worker.get("site_key") or "") != site_key:
                continue
            record = active_records.get(str(worker.get("work_item_id") or ""))
            if not record:
                continue
            worker = runtime.native_workers().update(
                worker["work_item_id"], control_epoch=int(record.get("control_epoch") or 0),
            )
            actions.extend(
                plan_worker_state(
                    worker=worker,
                    desired_state="running",
                    browser_policy="restore",
                )
            )
        scheduling = runtime.site_scheduler().reconcile()
        return {**result, "actions": actions, "scheduling": scheduling,
                "launch_required": not any(runtime.native_workers().get(work_item_id=work_id)
                                           for work_id in active_records)}

    @server.tool()
    def careereng_send_worker_command(
        site_key: str,
        message: str,
        interrupt_current_turn: bool = False,
        command_id: str = "",
    ) -> dict[str, Any]:
        """Persist ordered worker intent and materialize it at a safe boundary."""
        workers = [row for row in runtime.native_workers().list(active_only=True) if row.get("site_key") == site_key]
        if not workers:
            return {"ok": False, "error": f"no active native worker for site={site_key}"}
        worker = workers[-1]
        command = create_worker_command(
            command_id=str(command_id or make_id("worker_command")),
            site_key=site_key,
            batch_id=str(worker.get("batch_id") or ""),
            work_item_id=str(worker.get("work_item_id") or ""),
            kind=WorkerCommandKind.REDIRECT if interrupt_current_turn else WorkerCommandKind.GUIDANCE,
            message=message,
            expected_control_epoch=int(worker.get("control_epoch") or 0),
        )
        persisted, actions = runtime.worker_control().enqueue(command)
        return {
            "ok": True,
            "command": persisted.as_dict(),
            "actions": [row.as_dict() for row in actions],
            "action": actions[0].as_dict() if actions else {},
        }

    @server.tool()
    def careereng_pause_jobs_batch(batch_id: str, site_key: str = "") -> dict[str, Any]:
        """Pause a batch without converting its current site state into a blocker."""
        result = runtime.host_client().request(
            "pause_jobs_batch",
            {"batch_id": batch_id, "site_key": site_key},
        )
        if not result.get("ok"):
            return result
        actions = []
        for worker in workers_for_scope(batch_id=batch_id, site_key=site_key):
            actions.extend(plan_worker_state(worker=worker, desired_state="paused", browser_policy="retain"))
        return {**result, "actions": actions}

    @server.tool()
    def careereng_pause_site(batch_id: str, site_key: str) -> dict[str, Any]:
        """Pause one site worker while retaining its browser runtime."""
        return careereng_pause_jobs_batch(batch_id=batch_id, site_key=site_key)

    @server.tool()
    def careereng_stop_site(batch_id: str, site_key: str) -> dict[str, Any]:
        """Pause one site worker and release only its browser runtime."""
        result = runtime.host_client().request("stop_site", {"batch_id": batch_id, "site_key": site_key})
        if not result.get("ok"):
            return result
        actions = []
        for worker in workers_for_scope(batch_id=batch_id, site_key=site_key):
            actions.extend(plan_worker_state(worker=worker, desired_state="paused", browser_policy="release"))
        return {**result, "actions": actions}

    @server.tool()
    def careereng_cancel_site(batch_id: str, site_key: str, reason: str = "user_requested_cancel") -> dict[str, Any]:
        """Cancel one site without cancelling other sites in the batch."""
        result = runtime.host_client().request(
            "cancel_site",
            {"batch_id": batch_id, "site_key": site_key, "reason": reason},
        )
        actions = []
        for worker in workers_for_scope(batch_id=batch_id, site_key=site_key):
            actions.extend(plan_worker_state(worker=worker, desired_state="cancelled", browser_policy="release"))
        return {**result, "actions": actions}

    @server.tool()
    def careereng_set_site_mode(
        site_key: str,
        mode: str,
        apply_enabled: bool | None = None,
    ) -> dict[str, Any]:
        """Set draft/exploration/ready without deleting site history or browser state."""

        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode not in SITE_MODES:
            return {"ok": False, "error": f"unsupported site mode: {mode}"}
        site = runtime.site_store().find_site(site_key)
        if not site:
            return {"ok": False, "error": f"site not found: {site_key}"}
        resolved_key = str(site.get("site_key") or site_key)
        try:
            skill = runtime.site_store().set_skill_mode(
                resolved_key,
                mode=normalized_mode,
                apply_enabled=apply_enabled,
            )
        except (FileNotFoundError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        metadata = skill.get("front_matter") if isinstance(skill.get("front_matter"), dict) else {}
        return {
            "ok": True,
            "site_key": resolved_key,
            "mode": str(metadata.get("status") or ""),
            "apply_enabled": bool(metadata.get("apply_enabled")),
        }

    @server.tool()
    def careereng_cancel_jobs_batch(batch_id: str, reason: str = "user_requested_cancel") -> dict[str, Any]:
        """Cancel exactly one active batch and release only its site runtimes."""
        workers = workers_for_scope(batch_id=batch_id)
        result = runtime.host_client().request(
            "cancel_jobs_batch",
            {"batch_id": batch_id, "reason": reason},
        )
        actions = []
        for worker in workers:
            actions.extend(plan_worker_state(worker=worker, desired_state="cancelled", browser_policy="release"))
        return {**result, "actions": actions}

    @server.tool()
    def careereng_release_site(site_key: str) -> dict[str, Any]:
        """Release one retained site browser/runtime without changing CareerEng workflow state."""

        request = release_site_payload(site_key=site_key)
        return runtime.host_client().release_site(site_key=request["site_key"])

    @server.tool()
    def careereng_work_item_list_browser_tools(work_item_id: str) -> dict[str, Any]:
        """List browser tools available only inside one active worker scope."""
        try:
            scope = _active_work_item_scope(runtime, work_item_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        result = runtime.host_client().request(
            "agent_bridge_browser_list_tools",
            {"site_key": scope["site_key"], **_work_item_fence_payload(scope)},
        )
        return {**result, "work_item_id": scope["work_item_id"]}

    @server.tool()
    def careereng_work_item_call_browser_tool(
        work_item_id: str,
        context_revision: int,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call one browser tool inside the immutable scope of a worker item."""
        try:
            scope = _active_work_item_scope(
                runtime,
                work_item_id,
                expected_context_revision=context_revision,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        result = runtime.host_client().request(
            "agent_bridge_browser_call_tool",
            {
                "site_key": scope["site_key"],
                "tool_name": tool_name,
                "arguments": arguments or {},
                "turn_id": scope["turn_id"],
                "phase": scope["phase"],
                **_work_item_fence_payload(scope),
            },
        )
        return {**result, "work_item_id": scope["work_item_id"]}

    @server.tool()
    def careereng_work_item_run_browser_sequence(
        work_item_id: str,
        context_revision: int,
        steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Run explicit browser steps only inside the immutable worker scope."""
        try:
            scope = _active_work_item_scope(
                runtime,
                work_item_id,
                expected_context_revision=context_revision,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        result = runtime.host_client().request(
            "agent_bridge_browser_run_sequence",
            {
                "site_key": scope["site_key"],
                "steps": steps,
                "turn_id": scope["turn_id"],
                "phase": scope["phase"],
                **_work_item_fence_payload(scope),
            },
        )
        return {**result, "work_item_id": scope["work_item_id"]}

    @server.tool()
    def careereng_work_item_list_state_tools(work_item_id: str) -> dict[str, Any]:
        """List state tools available only for the current work-item phase."""
        try:
            scope = _active_work_item_scope(runtime, work_item_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        result = runtime.host_client().request(
            "agent_bridge_state_list_tools",
            {"site_key": scope["site_key"], "phase": scope["phase"], **_work_item_fence_payload(scope)},
        )
        return {**result, "work_item_id": scope["work_item_id"]}

    @server.tool()
    def careereng_work_item_call_state_tool(
        work_item_id: str,
        context_revision: int,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        apply_target_job_id: str = "",
    ) -> dict[str, Any]:
        """Call a state tool only inside the immutable worker scope."""
        try:
            scope = _active_work_item_scope(
                runtime,
                work_item_id,
                expected_context_revision=context_revision,
                expected_apply_target_job_id=apply_target_job_id,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if tool_name == "phase_result" and scope["phase"] == "apply":
            targets = scope["apply_target_job_ids"]
            if len(targets) != 1 or str(apply_target_job_id or "").strip() != targets[0]:
                return {"ok": False, "error": "apply phase result requires the active apply target fence"}
        result = runtime.host_client().request(
            "agent_bridge_state_call_tool",
            {
                "site_key": scope["site_key"],
                "tool_name": tool_name,
                "arguments": arguments or {},
                "turn_id": scope["turn_id"],
                "phase": scope["phase"],
                "apply_target_job_id": str(apply_target_job_id or "").strip(),
                **_work_item_fence_payload(scope),
            },
        )
        return {**result, "work_item_id": scope["work_item_id"]}

    @server.tool()
    def careereng_work_item_phase_result(
        work_item_id: str,
        context_revision: int,
        status: Literal["done", "waiting_user", "blocked"],
        summary: str,
        apply_target_job_id: str = "",
    ) -> dict[str, Any]:
        """Write the terminal result of exactly one active worker phase."""
        result = careereng_work_item_call_state_tool(
            work_item_id=work_item_id,
            context_revision=context_revision,
            tool_name="phase_result",
            arguments={"status": status, "summary": summary},
            apply_target_job_id=apply_target_job_id,
        )
        return {**result, "notification_policy": runtime.monitor_policy()}

    @server.tool()
    def careereng_complete_evolution_solution(work_item_id: str, run_id: str) -> dict[str, Any]:
        """Continue one worker after it has written and applied its current evolution proposal."""
        try:
            scope = _active_work_item_scope(runtime, work_item_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return runtime.host_client().request(
            "agent_bridge_evolution_solution_complete",
            {
                "site_key": scope["site_key"],
                "batch_id": scope["batch_id"],
                "run_id": str(run_id or ""),
            },
        )

    @server.tool()
    def careereng_submit_evolution_proposal(work_item_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        """Validate and persist one proposal for the active evolution summary."""
        try:
            scope = _active_work_item_scope(runtime, work_item_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not scope["evolution_run_id"]:
            return {"ok": False, "error": "work item is not an evolution summary"}
        return runtime.host_client().request(
            "agent_bridge_submit_evolution_proposal",
            {
                "site_key": scope["site_key"],
                "batch_id": scope["batch_id"],
                "run_id": scope["evolution_run_id"],
                "proposal": proposal if isinstance(proposal, dict) else {},
            },
        )

    @server.tool()
    def careereng_apply_evolution_solution(work_item_id: str, run_id: str) -> dict[str, Any]:
        """Apply the persisted proposal for the active evolution summary."""
        try:
            scope = _active_work_item_scope(runtime, work_item_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not scope["evolution_run_id"]:
            return {"ok": False, "error": "work item is not an evolution summary"}
        if str(run_id or "").strip() != scope["evolution_run_id"]:
            return {"ok": False, "error": "evolution run does not belong to this work item"}
        return runtime.host_client().request(
            "agent_bridge_apply_evolution_solution",
            {
                "site_key": scope["site_key"],
                "batch_id": scope["batch_id"],
                "run_id": scope["evolution_run_id"],
            },
        )

    return server


def run_mcp_server(
    *,
    project_root: Path | None = None,
    workspace: Path | None = None,
    transport: Literal["stdio", "sse", "streamable-http"] = "stdio",
    mount_path: str | None = None,
) -> None:
    server = create_mcp_server(project_root=project_root, workspace=workspace)
    server.run(transport=transport, mount_path=mount_path)
