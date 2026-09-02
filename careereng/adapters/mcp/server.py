"""Thin MCP server adapter for CareerEng tools.

The MCP layer is intentionally transport-only: it exposes existing CareerEng
manager and phase-runtime capabilities without adding workflow strategy.
"""

from __future__ import annotations

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
            "and the LLM."
        ),
    )

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
                include_notifications=include_notifications,
                limit=limit,
            ),
        }

    @server.tool()
    def careereng_ack_agent_events(cursor: str) -> dict[str, Any]:
        """Acknowledge agent events through a durable Desktop cursor."""
        try:
            return {"ok": True, **runtime.agent_events().acknowledge(consumer_id="codex_desktop", cursor=cursor)}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    @server.tool()
    def careereng_register_main_agent(thread_id: str) -> dict[str, Any]:
        """Register this Codex App Server thread as the workspace main agent."""
        try:
            registration = runtime.agent_events().register_main_agent(thread_id=thread_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        retry: dict[str, Any] = {}
        try:
            retry = runtime.host_client().main_agent_registration_updated()
        except Exception:
            # The registration is durable. A future host start retries pending
            # events even when the current host is intentionally offline.
            retry = {"deferred": True}
        return {"ok": True, **registration, "delivery_retry": retry}

    @server.tool()
    def careereng_get_main_agent_registration() -> dict[str, Any]:
        """Return the current workspace main-agent callback target."""
        return {"ok": True, "registration": runtime.agent_events().main_agent_registration()}

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
        return {"ok": True, **context}

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
        return result

    @server.tool()
    def careereng_resume_after_user_action(
        site_key: str,
        message: str = "",
        session_id: str = DEFAULT_SESSION_ID,
    ) -> dict[str, Any]:
        """Resume a waiting_user phase after the user completes a human-only browser action."""
        resume_message = str(message or "").strip() or f"{site_key} done"
        return runtime.host_client().request(
            "fresh_snapshot_resume",
            {
                "session_id": session_id,
                "message": resume_message,
                "turn_id": make_id("turn"),
                "site_key": site_key,
            },
        )

    @server.tool()
    def careereng_pause_jobs_batch(batch_id: str, site_key: str = "") -> dict[str, Any]:
        """Pause a batch without converting its current site state into a blocker."""
        return runtime.host_client().request(
            "pause_jobs_batch",
            {"batch_id": batch_id, "site_key": site_key},
        )

    @server.tool()
    def careereng_pause_site(batch_id: str, site_key: str) -> dict[str, Any]:
        """Pause one site worker while retaining its browser runtime."""
        return runtime.host_client().request("pause_site", {"batch_id": batch_id, "site_key": site_key})

    @server.tool()
    def careereng_stop_site(batch_id: str, site_key: str) -> dict[str, Any]:
        """Pause one site worker and release only its browser runtime."""
        return runtime.host_client().request("stop_site", {"batch_id": batch_id, "site_key": site_key})

    @server.tool()
    def careereng_cancel_site(batch_id: str, site_key: str, reason: str = "user_requested_cancel") -> dict[str, Any]:
        """Cancel one site without cancelling other sites in the batch."""
        return runtime.host_client().request(
            "cancel_site",
            {"batch_id": batch_id, "site_key": site_key, "reason": reason},
        )

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
        return runtime.host_client().request(
            "cancel_jobs_batch",
            {"batch_id": batch_id, "reason": reason},
        )

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
        return careereng_work_item_call_state_tool(
            work_item_id=work_item_id,
            context_revision=context_revision,
            tool_name="phase_result",
            arguments={"status": status, "summary": summary},
            apply_target_job_id=apply_target_job_id,
        )

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
