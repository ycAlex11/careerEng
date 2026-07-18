"""Thin MCP server adapter for CareerEng tools.

The MCP layer is intentionally transport-only: it exposes existing CareerEng
manager and phase-runtime capabilities without adding workflow strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from careereng.adapters.bootstrap import project_root_from_cwd, workspace_path as resolve_workspace_path
from careereng.adapters.external_agents.work_orders import load_active_phase_context
from careereng.adapters.external_agents.contracts import AGENT_BRIDGE_PROTOCOL_VERSION
from careereng.career.applications.job_store import JobStore, TERMINAL_BATCH_STATUSES
from careereng.career.applications.site_store import SiteStore
from careereng.platform.runtime_host import RUNTIME_HOST_PROTOCOL_VERSION, runtime_host_client, runtime_host_status
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

    def host_client(self):
        # The desktop adapter must never create a browser-owning process inside
        # its own sandbox. A user-owned Runtime Host is the only execution owner.
        return runtime_host_client(project_root=self.project_root, workspace=self.workspace, autostart=False)


def _compact_batch(batch: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(batch, dict) or not batch:
        return {}
    sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
    return {
        "batch_id": str(batch.get("batch_id") or ""),
        "session_id": str(batch.get("session_id") or ""),
        "turn_id": str(batch.get("turn_id") or ""),
        "operation": str(batch.get("operation") or ""),
        "apply_requested": bool(batch.get("apply_requested")),
        "status": str(batch.get("status") or ""),
        "created_at": str(batch.get("created_at") or ""),
        "updated_at": str(batch.get("updated_at") or ""),
        "site_count": len(sites),
        "sites": {
            str(site_key): _compact_batch_site(site)
            for site_key, site in sites.items()
            if isinstance(site, dict) and str(site_key)
        },
    }


def _compact_batch_site(site: dict[str, Any]) -> dict[str, Any]:
    retrieve = site.get("retrieve") if isinstance(site.get("retrieve"), dict) else {}
    apply = site.get("apply") if isinstance(site.get("apply"), dict) else {}
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


def _active_phase_contexts(
    *,
    site_store: SiteStore,
    batch: dict[str, Any] | None,
    site_key: str = "",
) -> list[dict[str, Any]]:
    """Read ready external-agent phase contexts without interpreting them."""

    if str((batch or {}).get("status") or "") in TERMINAL_BATCH_STATUSES:
        return []
    sites = batch.get("sites") if isinstance(batch, dict) and isinstance(batch.get("sites"), dict) else {}
    requested_site = str(site_key or "").strip()
    contexts: list[dict[str, Any]] = []
    for raw_key, row in sites.items():
        key = str(raw_key or "").strip()
        if not key or (requested_site and key != requested_site):
            continue
        try:
            browser_session = site_store.load_browser_session(key)
        except Exception:
            browser_session = {}
        payload_path = str(browser_session.get("agent_bridge_payload_path") or "")
        context = load_active_phase_context(payload_path)
        if context:
            contexts.append(context)
    return contexts


def _with_phase_contexts(payload: dict[str, Any], contexts: list[dict[str, Any]]) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["active_phase_contexts"] = contexts
    if len(contexts) == 1:
        enriched["active_phase_context"] = contexts[0]
    return enriched


def _wait_for_phase_contexts(
    *,
    runtime: CareerEngMCPRuntime,
    batch_id: str,
    timeout_seconds: float = 5.0,
) -> list[dict[str, Any]]:
    """Boundedly wait for a launched agent-bridge work order to become readable."""

    deadline = time.monotonic() + max(0.0, float(timeout_seconds or 0.0))
    while True:
        batch = runtime.job_store().load_batch(batch_id)
        contexts = _active_phase_contexts(site_store=runtime.site_store(), batch=batch)
        if contexts or time.monotonic() >= deadline:
            return contexts
        time.sleep(0.1)


def create_mcp_server(*, project_root: Path | None = None, workspace: Path | None = None) -> FastMCP:
    runtime = CareerEngMCPRuntime.from_paths(project_root=project_root, workspace=workspace)
    server = FastMCP(
        "careereng",
        instructions=(
            "CareerEng MCP tools expose local CareerEng workflow capabilities to Codex. "
            "Use these tools as orchestration/state/browser plumbing only; business judgment "
            "must stay in Skills, memory, evolution proposals, and the LLM."
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
    def careereng_get_context(
        session_id: str = DEFAULT_SESSION_ID,
        batch_id: str = "latest",
        site_key: str = "",
    ) -> dict[str, Any]:
        """Return compact local CareerEng session, batch, site, and browser-session context."""
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
        return _with_phase_contexts({
            "ok": True,
            "bridge_protocol_version": AGENT_BRIDGE_PROTOCOL_VERSION,
            "runtime_host_protocol_version": RUNTIME_HOST_PROTOCOL_VERSION,
            "project_root": str(runtime.project_root),
            "workspace": str(runtime.workspace),
            "session_id": session_id,
            "batch": _compact_batch(batch),
            "active_sites": compact_sites,
        }, _active_phase_contexts(site_store=site_store, batch=batch, site_key=site_key))

    @server.tool()
    def careereng_get_batch_status(
        batch_id: str = "latest",
        session_id: str = DEFAULT_SESSION_ID,
    ) -> dict[str, Any]:
        """Return compact status for one batch, or the latest open batch by default."""
        batch = _latest_batch(runtime.job_store(), session_id=session_id, batch_id=batch_id)
        return {"ok": True, "batch": _compact_batch(batch)}

    @server.tool()
    def careereng_start_jobs_batch(
        message: str = DEFAULT_JOBS_MESSAGE,
        operation: str = "job_search",
        apply_requested: bool = True,
        session_id: str = DEFAULT_SESSION_ID,
    ) -> dict[str, Any]:
        """Start a jobs batch and return its first ready external-agent phase context."""
        result = runtime.host_client().request(
            "start_jobs_batch",
            {
                "session_id": session_id,
                "message": message,
                "operation": operation,
                "apply_requested": bool(apply_requested),
            },
            timeout=10.0,
        )
        if not bool(result.get("accepted")):
            return result
        batch_id = str(result.get("batch_id") or "")
        contexts = _wait_for_phase_contexts(
            runtime=runtime,
            batch_id=batch_id,
        ) if batch_id else []
        return _with_phase_contexts(result, contexts)

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
    def careereng_list_browser_tools(site_key: str) -> dict[str, Any]:
        """List browser tools for an active CareerEng site runtime."""
        return runtime.host_client().request(
            "agent_bridge_browser_list_tools",
            {"site_key": site_key},
        )

    @server.tool()
    def careereng_call_browser_tool(
        site_key: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        phase: str = "external-agent-bridge",
        turn_id: str = "",
    ) -> dict[str, Any]:
        """Call one browser tool through the active CareerEng site runtime."""
        return runtime.host_client().request(
            "agent_bridge_browser_call_tool",
            {
                "site_key": site_key,
                "tool_name": tool_name,
                "arguments": arguments or {},
                "turn_id": turn_id,
                "phase": phase,
            },
        )

    @server.tool()
    def careereng_list_state_tools(site_key: str, phase: str = "") -> dict[str, Any]:
        """List CareerEng state tools for an active phase session."""
        return runtime.host_client().request(
            "agent_bridge_state_list_tools",
            {"site_key": site_key, "phase": phase},
        )

    @server.tool()
    def careereng_call_state_tool(
        site_key: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        phase: str = "",
        turn_id: str = "",
    ) -> dict[str, Any]:
        """Call one CareerEng state tool through the active phase session."""
        return runtime.host_client().request(
            "agent_bridge_state_call_tool",
            {
                "site_key": site_key,
                "tool_name": tool_name,
                "arguments": arguments or {},
                "turn_id": turn_id,
                "phase": phase,
            },
        )

    @server.tool()
    def careereng_phase_result(
        site_key: str,
        status: Literal["done", "blocked"],
        summary: str,
        phase: str = "",
        turn_id: str = "",
    ) -> dict[str, Any]:
        """Record a phase_result through the shared CareerEng state-tool path."""
        return runtime.host_client().request(
            "agent_bridge_state_call_tool",
            {
                "site_key": site_key,
                "tool_name": "phase_result",
                "arguments": {"status": status, "summary": summary},
                "turn_id": turn_id,
                "phase": phase,
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
