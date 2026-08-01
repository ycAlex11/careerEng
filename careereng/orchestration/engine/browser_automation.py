"""Site-level browser orchestration for browser-driven job phases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import shutil
import time
from typing import Any, Callable
import anyio

from careereng.platform.web_control import (
    MCPToolBridge,
    execute_browser_sequence,
    wait_for_process,
)
from careereng.platform.cache import CacheArtifactStore
from careereng.platform.sessions import BrowserProfileOwnerError, BrowserRuntimeLease, BrowserRuntimeRegistry
from careereng.adapters.external_agents.browser import browser_tool_commands
from careereng.adapters.external_agents.contracts import (
    AGENT_BRIDGE_MODE,
    CODEX_APP_SERVER_MODE,
    AGENT_BRIDGE_REQUIRED_REASON,
    AGENT_BRIDGE_STATUS,
    agent_bridge_phase,
    normalize_execution_mode,
)
from careereng.adapters.external_agents.work_orders import (
    advance_browser_agent_work_order,
    create_browser_agent_work_order,
    load_active_phase_context,
    persist_browser_agent_checkpoint,
    persist_browser_agent_phase_memory,
    refresh_browser_agent_work_order,
    set_browser_agent_work_order_state,
)
from careereng.orchestration.context.prompts import build_phase_prompts, load_text
from careereng.orchestration.agent_protocol.browser_phase import BrowserPhaseResult
from careereng.orchestration.agent_protocol.state_tools import state_tool_schemas_for_phase
from careereng.orchestration.agent_protocol.browser_sequence import (
    BROWSER_SEQUENCE_PHASES,
    BROWSER_SEQUENCE_TOOL,
    browser_sequence_tool_schema,
)
from careereng.orchestration.commands.state_tools import execute_state_tool
from careereng.orchestration.context import build_phase_context
from careereng.orchestration.engine import PhaseSequenceCompletion, advance_phase_sequence
from careereng.orchestration.engine.phase_orchestration import (
    is_pagination_action,
    retrieval_pagination_gate,
)
from careereng.orchestration.commands.state_tools import PhaseStateToolContext
from careereng.evolution.work_items import create_site_skill_refinement_card
from careereng.orchestration.context import (
    BrowserContextRegistry,
    BrowserContextSession,
    BrowserPhaseMemory,
    ContextResourceResolver,
    WorkflowMemoryStore,
    extract_failure_snapshot_from_trace,
)
from careereng.config.schema import (
    BrowserBudgetsConfig,
    BrowserGuardsConfig,
    BrowserRecoveryConfig,
    BrowserRetrievalPolicyConfig,
)
from careereng.career.resume.export import default_apply_resume_pdf_path
from careereng.platform.persistence import JSONLStore
from careereng.platform.observability import PerformanceRecorder
from careereng.utils import now_iso


SUPPORTED_PHASES = {
    "session_preparation",
    "application_status_review",
    "channel_discovery",
    "job_filtering",
    "job_retrieval",
    "apply",
}
DEFAULT_RUN_PHASES = ("session_preparation", "application_status_review", "channel_discovery")
GLOBAL_BLOCKED_TOOL_NAMES = {"browser_run_code"}
SESSION_PREPARATION_BLOCKED_TOOL_NAMES = {"browser_resize"}
JOB_RETRIEVAL_BLOCKED_TOOL_NAMES = {"browser_navigate"}


@dataclass(frozen=True)
class BrowserAutomationResult:
    site_key: str
    site_name: str
    status: str
    reason_tag: str
    message: str
    current_phase: str = ""
    current_url: str = ""
    trace_ref: str = ""
    step_count: int = 0
    retrieved_count: int = 0
    new_job_count: int = 0
    applied_count: int = 0
    submitted_count: int = 0
    already_applied_count: int = 0
    authenticated_ready: bool = False
    jobs_surface_ready: bool = False
    handoff_path: str = ""
    handoff_markdown_path: str = ""


class BrowserAutomationService:
    def __init__(
        self,
        *,
        project_root: Path,
        workspace: Path,
        site_store: Any,
        api_base: str,
        api_key: str,
        model: str,
        reasoning_effort: str,
        headless: bool,
        keep_open: bool,
        timeout_ms: int,
        phase_timeout_seconds: int,
        step_timeout_seconds: int,
        max_step_retries: int,
        max_phase_steps: int,
        browser_name: str,
        executable_path: str = "",
        execution_mode: str = "provider",
        budgets: BrowserBudgetsConfig | None = None,
        guards: BrowserGuardsConfig | None = None,
        recovery: BrowserRecoveryConfig | None = None,
        retrieval_policy: BrowserRetrievalPolicyConfig | None = None,
        phase_runtime_factory: Callable[[dict[str, Any]], Any] | None = None,
        evolution_signal_recorder: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ):
        self.project_root = Path(project_root).resolve()
        self.workspace = Path(workspace).resolve()
        self.site_store = site_store
        self._evolution_signal_recorder = evolution_signal_recorder
        self.execution_mode = self._normalize_execution_mode(execution_mode)
        self.headless = bool(headless)
        self.keep_open = bool(keep_open)
        self.timeout_ms = int(timeout_ms or 45000)
        self.browser_name = browser_name or "chrome"
        self.executable_path = str(executable_path or "").strip()
        self.guards = guards or BrowserGuardsConfig()
        self.recovery = recovery or BrowserRecoveryConfig()
        self.retrieval_policy = retrieval_policy or BrowserRetrievalPolicyConfig()
        self.budgets = budgets or BrowserBudgetsConfig(
            phase_timeout_seconds=int(phase_timeout_seconds or 180),
            step_timeout_seconds=int(step_timeout_seconds or 30),
            max_step_retries=int(max_step_retries or 1),
            max_phase_steps=int(max_phase_steps or 24),
        )
        self.phase_timeout_seconds = int(self.budgets.phase_timeout_seconds or phase_timeout_seconds or 180)
        self.max_phase_steps = int(self.budgets.max_phase_steps or max_phase_steps or 24)
        self._runtime_registry = BrowserRuntimeRegistry(
            runtime_root=self.workspace / "tmp" / "browser_controls",
            browser_name=self.browser_name,
            headless=self.headless,
            executable_path=self.executable_path,
            default_timeout_ms=self.timeout_ms,
        )
        self._browser_context_registry = BrowserContextRegistry(self.workspace)
        self._context_resource_scopes: set[tuple[str, str]] = set()
        self._cache_store = CacheArtifactStore(self.workspace)
        same_url_policy = getattr(self.guards, "same_url_no_progress", None)
        if isinstance(same_url_policy, dict):
            same_url_tool_limit = int(
                same_url_policy.get("tool_call_limit")
                or self.guards.same_url_no_progress_tool_call_limit
            )
            same_url_token_limit = int(
                same_url_policy.get("token_limit")
                or self.guards.same_url_no_progress_token_limit
            )
            same_url_phase_overrides = same_url_policy.get("phase_overrides") or {}
        else:
            same_url_tool_limit = int(
                getattr(same_url_policy, "tool_call_limit", self.guards.same_url_no_progress_tool_call_limit)
                or self.guards.same_url_no_progress_tool_call_limit
            )
            same_url_token_limit = int(
                getattr(same_url_policy, "token_limit", self.guards.same_url_no_progress_token_limit)
                or self.guards.same_url_no_progress_token_limit
            )
            same_url_phase_overrides = getattr(same_url_policy, "phase_overrides", {}) or {}
        self._phase_runtime_factory = phase_runtime_factory
        self._phase_runtime_settings = {
            "api_base": api_base,
            "api_key": api_key,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "phase_timeout_seconds": self.phase_timeout_seconds,
            "step_timeout_seconds": int(self.budgets.step_timeout_seconds or step_timeout_seconds or 30),
            "max_step_retries": int(self.budgets.max_step_retries or max_step_retries or 1),
            "max_phase_steps": self.max_phase_steps,
            "metrics_workspace": str(self.workspace),
            "retrieval_history_stop_success_ratio": float(self.retrieval_policy.history_stop_success_ratio),
            "retrieval_history_stop_min_page_jobs": int(self.retrieval_policy.history_stop_min_page_jobs),
            "same_url_no_progress_tool_call_limit": same_url_tool_limit,
            "same_url_no_progress_token_limit": same_url_token_limit,
            "apply_same_url_no_progress_tool_call_limit": int(self.guards.apply_same_url_no_progress_tool_call_limit),
            "apply_same_url_no_progress_token_limit": int(self.guards.apply_same_url_no_progress_token_limit),
            "same_url_no_progress_phase_overrides": dict(same_url_phase_overrides),
            "recovery_snapshot_timeout_seconds": int(self.recovery.snapshot_timeout_seconds),
            "recovery_max_attempts": int(self.recovery.max_attempts),
            "tool_settle_policies": dict(self.recovery.tool_settle_policies or {}),
        }
        self.phase_runtime: Any | None = None

    @staticmethod
    def _normalize_execution_mode(value: str) -> str:
        return normalize_execution_mode(value)

    async def _aclose_phase_runtime(self, runtime: Any) -> None:
        if runtime is None:
            return
        aclose = getattr(runtime, "aclose", None)
        if callable(aclose):
            result = aclose()
            if hasattr(result, "__await__"):
                await result
            return
        close = getattr(runtime, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result

    def close(self) -> None:
        active = self._runtime_registry.release_all()
        for item in active:
            self.site_store.save_browser_session(item.site_key, {"browser_status": "stopped", "active_run_id": ""})
        shared_phase_runtime = self.phase_runtime
        self.phase_runtime = None
        if shared_phase_runtime is not None:
            anyio.run(self._aclose_phase_runtime, shared_phase_runtime)

    def _project_skill_path(self) -> Path:
        return self.project_root / "skills" / "search" / "jobs" / "SKILL.md"

    def _cache_dependency_versions(self, site_key: str) -> dict[str, str]:
        versions = getattr(self.site_store, "decision_context_versions", None)
        if not callable(versions):
            return {}
        try:
            result = versions(site_key)
        except Exception:
            return {}
        return result if isinstance(result, dict) else {}

    def _cache_candidates(self, *, site_key: str, phase_slug: str, batch_id: str) -> list[dict[str, Any]]:
        return self._cache_store.lookup(
            scope={"site_key": site_key, "phase": phase_slug},
            dependency_versions=self._cache_dependency_versions(site_key),
            batch_id=batch_id,
        )

    def _site_skill_path(self, site_key: str) -> Path:
        load_skill = getattr(self.site_store, "load_skill", None)
        if callable(load_skill):
            skill = load_skill(site_key)
            path = skill.get("path") if isinstance(skill, dict) else None
            if path:
                return Path(path)
        site_skill_path = getattr(self.site_store, "site_skill_path", None)
        if callable(site_skill_path):
            return Path(site_skill_path(site_key))
        return self.site_store.site_dir(site_key) / "skills" / "SKILL.md"

    def _phase_prompts(self, site_key: str, *, allowed_slugs: set[str]):
        return build_phase_prompts(
            load_text(self._project_skill_path()),
            load_text(self._site_skill_path(site_key)),
            allowed_slugs=allowed_slugs,
        )

    def _run_site_agent_bridge(
        self,
        *,
        site_key: str,
        site_name: str,
        entry_url: str,
        session_id: str,
        turn_id: str,
        batch_id: str,
        resume: bool = False,
        phase_slugs: tuple[str, ...] | None = None,
        apply_target_job_ids: tuple[str, ...] | None = None,
        continuation_context: dict[str, Any] | None = None,
    ) -> BrowserAutomationResult:
        active, reused_runtime = self._reserve_runtime(site_key, entry_url)
        prior_session = self.site_store.load_browser_session(site_key)
        self.site_store.save_browser_session(
            site_key,
            {
                "browser_status": AGENT_BRIDGE_STATUS,
                "last_browser_pid": active.runtime.pid(),
                "last_browser_opened_at": now_iso(),
                "active_run_id": turn_id,
                "agent_bridge_session_id": session_id,
                "agent_bridge_batch_id": batch_id,
                "agent_bridge_turn_id": turn_id,
                "agent_bridge_current_phase": tuple(phase_slugs or DEFAULT_RUN_PHASES)[0],
                "agent_bridge_apply_target_job_ids": list(apply_target_job_ids or ()),
                "last_known_url": entry_url or active.entry_url or "",
                "current_trace_ref": "",
                "pending_action": AGENT_BRIDGE_STATUS,
                "current_step_id": "agent_bridge:browser_runtime_ready",
                "current_step_attempt": 0,
                "current_step_status": "waiting_external_agent",
                "expected_outcome": "The external agent should operate the retained CareerEng Playwright MCP runtime.",
                "last_step_error": "",
                "mcp_log_path": str(active.runtime.log_path),
                "runtime_reused": reused_runtime,
            },
        )
        self.site_store.append_event(
            site_key,
            "browser.agent_bridge.runtime_ready",
            {
                "turn_id": turn_id,
                "batch_id": batch_id,
                "pid": int(active.runtime.pid()),
                "run_id": active.runtime.run_id,
                "log_path": str(active.runtime.log_path),
                "reused_runtime": bool(reused_runtime),
            },
        )
        allowed_slugs = set(phase_slugs or DEFAULT_RUN_PHASES)
        phases = self._phase_prompts(site_key, allowed_slugs=allowed_slugs)
        apply_initial_facts: dict[str, Any] = {}
        if any(phase.slug == "apply" for phase in phases):
            try:
                staged_resume_pdf = self._stage_apply_resume_pdf(runtime_output_dir=active.runtime.output_dir)
            except Exception as exc:
                return BrowserAutomationResult(
                    site_key=site_key,
                    site_name=site_name,
                    status="failed",
                    reason_tag="resume_pdf_unavailable",
                    message=str(exc),
                )
            # Keep only execution hints in the persisted work item. Profile
            # facts remain an on-demand resource so a resumed agent gets the
            # user's latest values instead of a stale copied payload.
            apply_initial_facts = {
                "staged_resume": {
                    "path": str(staged_resume_pdf),
                    "filename": Path(staged_resume_pdf).name,
                },
                "apply_target_job_ids": [
                    str(job_id or "").strip()
                    for job_id in (apply_target_job_ids or ())
                    if str(job_id or "").strip()
                ],
            }
            self.site_store.save_browser_session(
                site_key,
                {"staged_resume_pdf_path": str(staged_resume_pdf)},
            )
        existing_payload_path = Path(str(prior_session.get("agent_bridge_payload_path") or ""))
        existing_phase_session_path = Path(str(prior_session.get("phase_session_path") or ""))
        same_batch = str(prior_session.get("agent_bridge_batch_id") or "") == str(batch_id or "")
        common_kwargs = {
            "workspace": self.workspace,
            "entry_url": entry_url,
            "phase_slugs": tuple(phase_slugs or DEFAULT_RUN_PHASES),
            "phases": phases,
            "apply_target_job_ids": apply_target_job_ids,
            "continuation_context": continuation_context,
            "tool_commands": browser_tool_commands(site_key),
            "cache_candidates": self._cache_candidates(site_key=site_key, phase_slug=phases[0].slug if phases else "", batch_id=batch_id),
            "cache_dependency_versions": self._cache_dependency_versions(site_key),
            "apply_initial_facts": apply_initial_facts,
        }
        if same_batch and existing_payload_path.is_file() and existing_phase_session_path.is_file():
            work_order = refresh_browser_agent_work_order(
                payload_path=existing_payload_path,
                phase_session_path=existing_phase_session_path,
                **common_kwargs,
            )
        else:
            work_order = create_browser_agent_work_order(
                site_store=self.site_store,
                site_key=site_key,
                site_name=site_name,
                session_id=session_id,
                turn_id=turn_id,
                batch_id=batch_id,
                resume=resume,
                project_skill_path=self._project_skill_path(),
                site_skill_path=self._site_skill_path(site_key),
                **common_kwargs,
            )
        return BrowserAutomationResult(
            site_key=site_key,
            site_name=site_name,
            status="blocked",
            reason_tag=AGENT_BRIDGE_REQUIRED_REASON,
            message=work_order.message,
            current_phase=work_order.current_phase,
            current_url=entry_url or "",
            handoff_path=str(work_order.payload_path),
            handoff_markdown_path=str(work_order.markdown_path),
        )

    def _active_runtime_for_agent_bridge(self, site_key: str) -> BrowserRuntimeLease:
        normalized_site = str(site_key or "").strip()
        if not normalized_site:
            raise RuntimeError("site is required")
        return self._runtime_registry.active(normalized_site)

    async def _list_active_browser_tools_async(self, site_key: str) -> list[dict[str, Any]]:
        active = self._active_runtime_for_agent_bridge(site_key)
        bridge = MCPToolBridge(active.runtime, timeout_seconds=max(30.0, self.timeout_ms / 1000.0))
        async with bridge.open_session() as session:
            tools = await bridge.list_tools(session)
        rows: list[dict[str, Any]] = []
        for tool in tools:
            rows.append(
                {
                    "name": str(getattr(tool, "name", "") or ""),
                    "description": str(getattr(tool, "description", "") or getattr(tool, "title", "") or ""),
                    "schema": getattr(tool, "inputSchema", {}) if isinstance(getattr(tool, "inputSchema", {}), dict) else {},
                }
            )
        return rows

    def list_active_browser_tools(self, site_key: str) -> list[dict[str, Any]]:
        return anyio.run(self._list_active_browser_tools_async, site_key)

    async def _call_active_browser_tool_async(
        self,
        *,
        site_key: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        turn_id: str = "",
        phase: str = AGENT_BRIDGE_STATUS,
    ) -> dict[str, Any]:
        started = time.monotonic()
        session_context = self._agent_bridge_session_context(site_key, phase=phase)
        effective_phase = agent_bridge_phase(phase)
        phase_memory = self._load_agent_bridge_phase_memory(session_context)
        if is_pagination_action(tool_name, arguments):
            pagination_gate = retrieval_pagination_gate(phase_slug=effective_phase, phase_memory=phase_memory)
            if not pagination_gate.allowed:
                return {
                    "ok": False,
                    "tool_name": tool_name,
                    "current_url": str(session_context.get("current_url") or ""),
                    "summary": pagination_gate.message,
                    "error": "retrieval_history_stop_required",
                    "payload": {"isError": True, "error": "retrieval_history_stop_required"},
                }
        active = self._active_runtime_for_agent_bridge(site_key)
        bridge = MCPToolBridge(active.runtime, timeout_seconds=max(30.0, self.timeout_ms / 1000.0))
        try:
            async with bridge.open_session() as session:
                payload = await bridge.call_tool(session, tool_name, arguments or {})
        except Exception as exc:
            PerformanceRecorder(self.workspace).record(
                backend="external_agent",
                operation="browser_tool",
                tool_name=tool_name,
                site_key=site_key,
                batch_id=str(session_context.get("batch_id") or ""),
                phase=effective_phase,
                status="error",
                error_type=exc.__class__.__name__,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                observation_kind="full" if tool_name == "browser_snapshot" else "",
            )
            raise
        current_url = bridge.extract_current_url(payload) or ""
        summary = bridge.summarize_tool_output(payload)
        trace_ref = self.site_store.append_step_trace(
            site_key,
            turn_id or AGENT_BRIDGE_STATUS,
            {
                "phase": effective_phase,
                "step_id": f"agent_bridge:{tool_name}",
                "attempt": 1,
                "tool_name": tool_name,
                "arguments": arguments or {},
                "result": "error" if payload.get("isError") else "ok",
                "output": summary,
            },
        )
        self._persist_agent_bridge_checkpoint(
            session_context,
            {
                "phase": effective_phase,
                "tool_name": tool_name,
                "trace_ref": trace_ref,
                "current_url": current_url,
                "status": "error" if payload.get("isError") else "ok",
                "recorded_at": now_iso(),
            },
        )
        session_update = {
            "browser_status": AGENT_BRIDGE_STATUS,
            "pending_action": AGENT_BRIDGE_STATUS,
            "current_step_id": f"agent_bridge:{tool_name}",
            "current_step_status": "tool_error" if payload.get("isError") else "tool_ok",
            "current_trace_ref": trace_ref,
            "last_step_error": summary[:1000] if payload.get("isError") else "",
        }
        if current_url:
            session_update["last_known_url"] = current_url
        self.site_store.save_browser_session(site_key, session_update)
        self.site_store.append_event(
            site_key,
            "browser.agent_bridge.tool_called",
            {
                "turn_id": turn_id or AGENT_BRIDGE_STATUS,
                "phase": effective_phase,
                "tool_name": tool_name,
                "result": "error" if payload.get("isError") else "ok",
                "current_url": current_url,
                "trace_ref": trace_ref,
            },
        )
        PerformanceRecorder(self.workspace).record(
            backend="external_agent",
            operation="browser_tool",
            tool_name=tool_name,
            site_key=site_key,
            batch_id=str(session_context.get("batch_id") or ""),
            phase=effective_phase,
            status="error" if payload.get("isError") else "ok",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            observation_kind="full" if tool_name == "browser_snapshot" else "",
        )
        return {
            "ok": not bool(payload.get("isError")),
            "tool_name": tool_name,
            "current_url": current_url,
            "summary": summary,
            "trace_ref": trace_ref,
            "payload": payload,
        }

    async def _run_active_browser_sequence_async(
        self,
        *,
        site_key: str,
        steps: list[dict[str, Any]],
        turn_id: str = "",
        phase: str = AGENT_BRIDGE_STATUS,
    ) -> dict[str, Any]:
        """Run agent-supplied browser actions in order without site interpretation."""

        started = time.monotonic()
        context = self._agent_bridge_session_context(site_key, phase=phase)
        effective_phase = agent_bridge_phase(phase)
        phase_memory = self._load_agent_bridge_phase_memory(context)
        if any(
            is_pagination_action(str(step.get("tool_name") or step.get("tool") or ""), step.get("arguments") if isinstance(step.get("arguments"), dict) else {})
            for step in steps
            if isinstance(step, dict)
        ):
            pagination_gate = retrieval_pagination_gate(phase_slug=effective_phase, phase_memory=phase_memory)
            if not pagination_gate.allowed:
                return {
                    "ok": False,
                    "tool_name": BROWSER_SEQUENCE_TOOL,
                    "current_url": str(context.get("current_url") or ""),
                    "summary": pagination_gate.message,
                    "error": "retrieval_history_stop_required",
                    "payload": {"isError": True, "error": "retrieval_history_stop_required"},
                }
        active = self._active_runtime_for_agent_bridge(site_key)
        bridge = MCPToolBridge(active.runtime, timeout_seconds=max(30.0, self.timeout_ms / 1000.0))
        async with bridge.open_session() as session:
            payload = await execute_browser_sequence(
                steps=steps,
                call_browser_tool=lambda name, arguments: bridge.call_tool(session, name, arguments),
            )
        last_payload = payload.get("last_payload") if isinstance(payload.get("last_payload"), dict) else {}
        current_url = bridge.extract_current_url(last_payload)
        summary = json.dumps(
            {
                "completed": payload.get("completed") or 0,
                "error": payload.get("error") or "",
                "steps": [
                    {"tool_name": row.get("tool_name"), "is_error": row.get("is_error")}
                    for row in payload.get("steps", [])
                    if isinstance(row, dict)
                ],
            },
            ensure_ascii=False,
        )
        trace_ref = self.site_store.append_step_trace(
            site_key,
            turn_id or AGENT_BRIDGE_STATUS,
            {
                "phase": effective_phase,
                "step_id": "agent_bridge:browser_sequence",
                "attempt": 1,
                "tool_name": BROWSER_SEQUENCE_TOOL,
                "arguments": {"steps": steps},
                "result": "error" if payload.get("isError") else "ok",
                "output": summary,
            },
        )
        self._persist_agent_bridge_checkpoint(
            context,
            {
                "phase": effective_phase,
                "tool_name": BROWSER_SEQUENCE_TOOL,
                "trace_ref": trace_ref,
                "current_url": current_url,
                "status": "error" if payload.get("isError") else "ok",
                "recorded_at": now_iso(),
            },
        )
        self.site_store.save_browser_session(
            site_key,
            {
                "current_step_id": "agent_bridge:browser_sequence",
                "current_step_status": "tool_error" if payload.get("isError") else "tool_ok",
                "current_trace_ref": trace_ref,
                "last_step_error": str(payload.get("error") or "")[:1000],
                **({"last_known_url": current_url} if current_url else {}),
            },
        )
        PerformanceRecorder(self.workspace).record(
            backend="external_agent",
            operation="browser_sequence",
            tool_name=BROWSER_SEQUENCE_TOOL,
            site_key=site_key,
            batch_id=str(context.get("batch_id") or ""),
            phase=effective_phase,
            status="error" if payload.get("isError") else "ok",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            sequence_step_count=len(steps),
        )
        return {
            "ok": not bool(payload.get("isError")),
            "tool_name": BROWSER_SEQUENCE_TOOL,
            "current_url": current_url,
            "summary": summary,
            "trace_ref": trace_ref,
            "payload": payload,
        }

    def run_active_browser_sequence(
        self,
        *,
        site_key: str,
        steps: list[dict[str, Any]],
        turn_id: str = "",
        phase: str = AGENT_BRIDGE_STATUS,
    ) -> dict[str, Any]:
        async def _runner() -> dict[str, Any]:
            return await self._run_active_browser_sequence_async(
                site_key=site_key,
                steps=steps,
                turn_id=turn_id,
                phase=phase,
            )

        return anyio.run(_runner)

    def call_active_browser_tool(
        self,
        *,
        site_key: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        turn_id: str = "",
        phase: str = AGENT_BRIDGE_STATUS,
    ) -> dict[str, Any]:
        async def _runner() -> dict[str, Any]:
            return await self._call_active_browser_tool_async(
                site_key=site_key,
                tool_name=tool_name,
                arguments=arguments or {},
                turn_id=turn_id,
                phase=phase,
            )

        return anyio.run(_runner)

    def _agent_bridge_session_context(self, site_key: str, *, phase: str = "") -> dict[str, Any]:
        normalized_site = str(site_key or "").strip()
        if not normalized_site:
            raise RuntimeError("site is required")
        load_session = getattr(self.site_store, "load_browser_session", None)
        browser_session = load_session(normalized_site) if callable(load_session) else {}
        if not isinstance(browser_session, dict):
            browser_session = {}
        current_phase = str(phase or browser_session.get("agent_bridge_current_phase") or AGENT_BRIDGE_STATUS).strip() or AGENT_BRIDGE_STATUS
        return {
            "site_key": normalized_site,
            "phase": current_phase,
            "batch_id": str(browser_session.get("agent_bridge_batch_id") or ""),
            "session_id": str(browser_session.get("agent_bridge_session_id") or ""),
            "turn_id": str(browser_session.get("agent_bridge_turn_id") or browser_session.get("active_run_id") or ""),
            "current_url": str(browser_session.get("last_known_url") or ""),
            "phase_session_path": str(browser_session.get("phase_session_path") or ""),
            "payload_path": str(browser_session.get("agent_bridge_payload_path") or ""),
        }

    @staticmethod
    def _agent_bridge_apply_initial_facts(context: dict[str, Any]) -> dict[str, Any]:
        payload_path = Path(str(context.get("payload_path") or ""))
        if not payload_path.is_file():
            return {}
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        phase_context = payload.get("current_phase_context") if isinstance(payload, dict) else {}
        if isinstance(phase_context, dict) and isinstance(phase_context.get("apply_facts"), dict):
            return dict(phase_context["apply_facts"])
        return dict(payload.get("apply_initial_facts") or {}) if isinstance(payload, dict) else {}

    @staticmethod
    def _load_agent_bridge_phase_memory(context: dict[str, Any]) -> BrowserPhaseMemory:
        phase_session_path = Path(str(context.get("phase_session_path") or ""))
        if not phase_session_path.is_file():
            return BrowserPhaseMemory()
        try:
            payload = json.loads(phase_session_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        return BrowserPhaseMemory.from_payload(payload.get("phase_memory") if isinstance(payload, dict) else {})

    @staticmethod
    def _persist_agent_bridge_phase_memory(context: dict[str, Any], phase_memory: BrowserPhaseMemory) -> None:
        payload_path = Path(str(context.get("payload_path") or ""))
        phase_session_path = Path(str(context.get("phase_session_path") or ""))
        if not payload_path.is_file() or not phase_session_path.is_file():
            raise RuntimeError("agent bridge phase session is unavailable")
        persist_browser_agent_phase_memory(
            payload_path=payload_path,
            phase_session_path=phase_session_path,
            phase_memory=phase_memory.as_payload(),
        )

    @staticmethod
    def _persist_agent_bridge_checkpoint(context: dict[str, Any], checkpoint: dict[str, Any]) -> None:
        payload_path = Path(str(context.get("payload_path") or ""))
        phase_session_path = Path(str(context.get("phase_session_path") or ""))
        if not payload_path.is_file() or not phase_session_path.is_file():
            return
        persist_browser_agent_checkpoint(
            payload_path=payload_path,
            phase_session_path=phase_session_path,
            checkpoint=checkpoint,
        )

    def _advance_active_agent_bridge_phase(
        self,
        *,
        site_key: str,
        context: dict[str, Any],
        result_status: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Apply only generic phase progression after an external phase_result."""

        phase_session_path = Path(str(context.get("phase_session_path") or ""))
        payload_path = Path(str(context.get("payload_path") or ""))
        if not phase_session_path.is_file() or not payload_path.is_file():
            return (
                {"action": "missing_session", "current_phase": str(context.get("phase") or "")},
                {},
            )
        try:
            phase_session = json.loads(phase_session_path.read_text(encoding="utf-8"))
        except Exception:
            phase_session = {}
        if not isinstance(phase_session, dict):
            phase_session = {}
        transition = advance_phase_sequence(
            tuple(phase_session.get("phase_slugs") or ()),
            current_phase=str(context.get("phase") or phase_session.get("current_phase") or ""),
            result_status=result_status,
        )
        transition_payload = transition.as_dict()
        if transition.action == "advance_phase":
            work_order = advance_browser_agent_work_order(
                workspace=self.workspace,
                payload_path=payload_path,
                phase_session_path=phase_session_path,
                next_phase=transition.next_phase,
            )
            transition_payload["active_phase_context"] = load_active_phase_context(work_order.payload_path)
            return (
                transition_payload,
                {
                    "agent_bridge_current_phase": transition.next_phase,
                    "current_step_id": f"agent_bridge:phase:{transition.next_phase}",
                    "current_step_status": "waiting_external_agent",
                    "pending_action": AGENT_BRIDGE_STATUS,
                    "resume_phase": "",
                    "phase_session_path": str(phase_session_path),
                    "agent_bridge_payload_path": str(work_order.payload_path),
                    "agent_bridge_work_order_path": str(work_order.markdown_path),
                },
            )
        if transition.action == "continue_current":
            set_browser_agent_work_order_state(
                workspace=self.workspace,
                payload_path=payload_path,
                phase_session_path=phase_session_path,
                worker_state="waiting_user",
            )
            transition_payload["active_phase_context"] = load_active_phase_context(payload_path)
            return (
                transition_payload,
                {
                    "agent_bridge_current_phase": transition.current_phase,
                    "current_step_id": f"agent_bridge:phase:{transition.current_phase}",
                    "current_step_status": "waiting_external_agent",
                    "pending_action": AGENT_BRIDGE_STATUS,
                    "resume_phase": transition.current_phase,
                },
            )
        if transition.action == "complete_sequence":
            set_browser_agent_work_order_state(
                workspace=self.workspace,
                payload_path=payload_path,
                phase_session_path=phase_session_path,
                worker_state="transitioning",
            )
            completion = PhaseSequenceCompletion(
                site_key=str(context.get("site_key") or site_key),
                batch_id=str(context.get("batch_id") or ""),
                session_id=str(context.get("session_id") or ""),
                turn_id=str(context.get("turn_id") or ""),
                terminal_phase=transition.current_phase,
            )
            transition_payload["completion"] = completion.as_dict()
            return (
                transition_payload,
                {
                    "agent_bridge_current_phase": "",
                    "current_step_id": "agent_bridge:phase_sequence_complete",
                    "current_step_status": "phase_sequence_complete",
                    "pending_action": "",
                    "resume_phase": "",
                },
            )
        return transition_payload, {}

    def list_active_state_tools(self, site_key: str, *, phase: str = "") -> list[dict[str, Any]]:
        context = self._agent_bridge_session_context(site_key, phase=phase)
        return state_tool_schemas_for_phase(str(context.get("phase") or ""), include_phase_result=True)

    def set_evolution_signal_recorder(self, recorder: Callable[[dict[str, Any]], dict[str, Any]] | None) -> None:
        """Attach the generic evolution engine after the workflow is built."""

        self._evolution_signal_recorder = recorder

    def _record_active_evolution_signal(
        self,
        *,
        context: dict[str, Any],
        phase: str,
        turn_id: str,
        signal: dict[str, Any],
    ) -> dict[str, Any]:
        recorder = self._evolution_signal_recorder
        if not callable(recorder):
            raise RuntimeError("evolution signal recorder is unavailable")
        return recorder(
            {
                "site_key": str(context.get("site_key") or ""),
                "batch_id": str(context.get("batch_id") or ""),
                "phase": str(phase or ""),
                "turn_id": str(turn_id or ""),
                "trace_ref": str(context.get("current_trace_ref") or ""),
                "current_url": str(context.get("current_url") or ""),
                "signal": dict(signal or {}),
            }
        )

    def call_active_state_tool(
        self,
        *,
        site_key: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        turn_id: str = "",
        phase: str = "",
    ) -> dict[str, Any]:
        started = time.monotonic()
        context = self._agent_bridge_session_context(site_key, phase=phase)
        effective_turn_id = str(turn_id or context.get("turn_id") or AGENT_BRIDGE_STATUS)
        effective_phase = str(context.get("phase") or phase or AGENT_BRIDGE_STATUS)
        phase_memory = self._load_agent_bridge_phase_memory(context)
        cache_versions = self._cache_dependency_versions(str(context.get("site_key") or site_key))
        context_resources = None
        if effective_phase == "apply":
            self._retain_context_resource_scope(site_key=str(context.get("site_key") or site_key), batch_id=str(context.get("batch_id") or ""))
            self._browser_context_registry.refresh_if_changed()
            context_resources = ContextResourceResolver.create(
                workspace=self.workspace,
                site_store=self.site_store,
                site_key=str(context.get("site_key") or site_key),
                batch_id=str(context.get("batch_id") or ""),
                registry=self._browser_context_registry,
                apply_initial_facts=self._agent_bridge_apply_initial_facts(context),
            )
        payload = execute_state_tool(
            tool_name,
            arguments or {},
            PhaseStateToolContext(
                site_store=self.site_store,
                site_key=str(context.get("site_key") or site_key),
                session_id=str(context.get("session_id") or ""),
                turn_id=effective_turn_id,
                batch_id=str(context.get("batch_id") or ""),
                current_url=str(context.get("current_url") or ""),
                phase_slug=effective_phase,
                context_session=context_resources,
                phase_memory=phase_memory,
                persist_phase_memory=lambda memory: self._persist_agent_bridge_phase_memory(context, memory),
                workspace=self.workspace,
                cache_scope={"site_key": str(context.get("site_key") or site_key), "phase": effective_phase},
                cache_dependency_versions=cache_versions,
                record_evolution_signal=lambda signal: self._record_active_evolution_signal(
                    context=context,
                    phase=effective_phase,
                    turn_id=effective_turn_id,
                    signal=signal,
                ),
            ),
        )
        summary = MCPToolBridge.summarize_tool_output(payload)
        trace_ref = self.site_store.append_step_trace(
            str(context.get("site_key") or site_key),
            effective_turn_id,
            {
                "phase": effective_phase,
                "step_id": f"agent_bridge_state:{tool_name}",
                "attempt": 1,
                "tool_name": tool_name,
                "arguments": arguments or {},
                "result": "error" if payload.get("isError") else "ok",
                "output": summary,
            },
        )
        session_update = {
            "browser_status": AGENT_BRIDGE_STATUS,
            "pending_action": AGENT_BRIDGE_STATUS,
            "current_step_id": f"agent_bridge_state:{tool_name}",
            "current_step_status": "tool_error" if payload.get("isError") else "tool_ok",
            "current_trace_ref": trace_ref,
            "last_step_error": summary[:1000] if payload.get("isError") else "",
        }
        progression: dict[str, Any] = {}
        if str(tool_name or "").strip() == "phase_result" and not payload.get("isError"):
            structured = payload.get("structuredContent") if isinstance(payload.get("structuredContent"), dict) else {}
            session_update["current_step_status"] = f"phase_{str(structured.get('status') or 'result')}"
            progression, progression_session_update = self._advance_active_agent_bridge_phase(
                site_key=str(context.get("site_key") or site_key),
                context=context,
                result_status=str(structured.get("status") or ""),
            )
            session_update.update(progression_session_update)
        self.site_store.save_browser_session(str(context.get("site_key") or site_key), session_update)
        self.site_store.append_event(
            str(context.get("site_key") or site_key),
            "browser.agent_bridge.state_tool_called",
            {
                "turn_id": effective_turn_id,
                "phase": effective_phase,
                "tool_name": tool_name,
                "result": "error" if payload.get("isError") else "ok",
                "trace_ref": trace_ref,
                "progression": progression,
            },
        )
        PerformanceRecorder(self.workspace).record(
            backend="external_agent",
            operation="state_tool",
            tool_name=tool_name,
            site_key=str(context.get("site_key") or site_key),
            batch_id=str(context.get("batch_id") or ""),
            phase=effective_phase,
            status="error" if payload.get("isError") else "ok",
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        return {
            "ok": not bool(payload.get("isError")),
            "tool_name": tool_name,
            "phase": effective_phase,
            "trace_ref": trace_ref,
            "summary": summary,
            "payload": payload,
            "progression": progression,
        }

    def read_active_context_resource(
        self,
        *,
        site_key: str,
        resource_id: str,
        phase: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        """Read an agent-selected resource from the retained site/batch scope."""

        context = self._agent_bridge_session_context(site_key, phase=phase)
        self._retain_context_resource_scope(
            site_key=str(context.get("site_key") or site_key),
            batch_id=str(context.get("batch_id") or ""),
        )
        self._browser_context_registry.refresh_if_changed()
        resolver = ContextResourceResolver.create(
            workspace=self.workspace,
            site_store=self.site_store,
            site_key=str(context.get("site_key") or site_key),
            batch_id=str(context.get("batch_id") or ""),
            registry=self._browser_context_registry,
            apply_initial_facts=self._agent_bridge_apply_initial_facts(context),
        )
        return resolver.read(resource_id, reason=reason)

    @staticmethod
    def _phase_has_jobs_surface(phase_slug: str) -> bool:
        return str(phase_slug or "").strip() in {"channel_discovery", "job_filtering", "job_retrieval"}

    @classmethod
    def _ready_message_for_phase(cls, phase_slug: str, *, authenticated_ready: bool, jobs_surface_ready: bool) -> str:
        normalized = str(phase_slug or "").strip()
        if authenticated_ready:
            if normalized == "apply":
                return "登录已就绪，岗位投递已完成。"
            if normalized == "job_retrieval":
                return "登录已就绪，岗位检索已完成，等待后续投递。"
            if normalized == "job_filtering":
                return "登录已就绪，岗位筛选已完成，等待后续岗位检索。"
            if normalized == "channel_discovery":
                return "登录已就绪，岗位入口已定位，等待后续岗位检索。"
            return "登录已就绪，等待后续岗位检索。"
        if jobs_surface_ready:
            if normalized == "apply":
                return "岗位投递已完成。"
            if normalized == "job_retrieval":
                return "岗位检索已完成，当前 jobs 页面可继续，等待后续投递。"
            if normalized == "job_filtering":
                return "岗位筛选已完成，当前 jobs 页面可继续，等待后续岗位检索。"
            if normalized == "channel_discovery":
                return "岗位入口已定位，当前 jobs 页面可继续，等待后续岗位检索。"
        return "当前 jobs 页面可继续，等待后续岗位检索。"

    @staticmethod
    def _tool_allowed_for_phase(tool_name: str, phase_slug: str) -> bool:
        normalized = str(tool_name or "").strip()
        if not normalized:
            return False
        if normalized in GLOBAL_BLOCKED_TOOL_NAMES:
            return False
        if phase_slug == "session_preparation" and normalized in SESSION_PREPARATION_BLOCKED_TOOL_NAMES:
            return False
        if phase_slug == "job_retrieval" and normalized in JOB_RETRIEVAL_BLOCKED_TOOL_NAMES:
            return False
        return True

    def _phase_timeout_seconds(self, phase_slug: str, *, phase_memory: BrowserPhaseMemory | None = None) -> int:
        base = int(self.budgets.phase_timeout_seconds or self.phase_timeout_seconds or 180)
        normalized_phase = str(phase_slug or "").strip()
        if normalized_phase == "session_preparation":
            return max(base, int(self.budgets.session_preparation_phase_timeout_seconds or base))
        if normalized_phase == "application_status_review":
            return max(base, int(self.budgets.application_status_review_phase_timeout_seconds or base))
        if normalized_phase == "job_retrieval":
            timeout = max(base, int(self.budgets.job_retrieval_phase_timeout_seconds or base))
            if isinstance(phase_memory, BrowserPhaseMemory):
                budget_pages = phase_memory.retrieval_budget_pages(
                    default_page_size=10,
                    max_pages=int(self.budgets.job_retrieval_timeout_max_pages or 10),
                )
                if budget_pages:
                    timeout = max(timeout, int(budget_pages) * int(self.budgets.job_retrieval_timeout_seconds_per_page or 180))
            return timeout
        if normalized_phase == "apply":
            return max(base, int(self.budgets.apply_phase_timeout_seconds or base))
        if normalized_phase == "job_filtering":
            return max(base, int(self.budgets.job_filtering_phase_timeout_seconds or base))
        return base

    def _phase_max_steps(self, phase_slug: str) -> int:
        base = int(self.budgets.max_phase_steps or self.max_phase_steps or 24)
        if str(phase_slug or "").strip() == "job_retrieval":
            return max(base, int(self.budgets.job_retrieval_max_phase_steps or base))
        if str(phase_slug or "").strip() == "apply":
            return max(base, int(self.budgets.apply_max_phase_steps or base))
        return base

    def _response_tools_for_phase(self, tools: list[Any], phase_slug: str) -> tuple[list[dict[str, Any]], set[str]]:
        response_tools: list[dict[str, Any]] = []
        tool_names: set[str] = set()
        for tool in tools:
            tool_name = str(getattr(tool, "name", "") or "").strip()
            if not self._tool_allowed_for_phase(tool_name, phase_slug):
                continue
            schema = MCPToolBridge.tool_to_function_schema(tool)
            response_tools.append(schema)
            name = str(schema.get("name", "") or "").strip()
            if name:
                tool_names.add(name)
        for schema in state_tool_schemas_for_phase(phase_slug):
            response_tools.append(schema)
            name = str(schema.get("name") or "").strip()
            if name:
                tool_names.add(name)
        if phase_slug in BROWSER_SEQUENCE_PHASES:
            response_tools.append(browser_sequence_tool_schema())
            tool_names.add(BROWSER_SEQUENCE_TOOL)
        return response_tools, tool_names

    def _phase_context_session(
        self,
        *,
        site_key: str,
        phase_slug: str,
        batch_id: str,
        target_job_ids: tuple[str, ...] | None = None,
        staged_resume_pdf_path: str = "",
        phase_memory: BrowserPhaseMemory | None = None,
        continuation_context: dict[str, Any] | None = None,
    ) -> BrowserContextSession | None:
        if phase_slug == "apply":
            self._retain_context_resource_scope(site_key=site_key, batch_id=batch_id)
            self._browser_context_registry.refresh_if_changed()
            return BrowserContextSession.for_apply(
                registry=self._browser_context_registry,
                workspace=self.workspace,
                site_store=self.site_store,
                site_key=site_key,
                batch_id=batch_id,
                target_job_ids=target_job_ids,
                staged_resume_pdf_path=staged_resume_pdf_path,
                phase_memory=phase_memory,
                continuation_context=continuation_context,
            )
        return BrowserContextSession.for_phase(
            phase_memory=phase_memory,
            continuation_context=continuation_context,
            workspace=self.workspace,
            site_key=site_key,
            batch_id=batch_id,
            phase=phase_slug,
        )

    def _session_preparation_context_items(self, site_key: str) -> list[dict[str, str]]:
        resume_pdf_path = default_apply_resume_pdf_path(self.workspace)
        resume_updated_at = self._current_apply_resume_pdf_updated_at(resume_pdf_path)
        if not resume_updated_at:
            return []
        last_preparation_at = self._last_successful_session_preparation_at(site_key)
        upload_needed = self._resume_upload_needed(
            resume_updated_at=resume_updated_at,
            last_preparation_at=last_preparation_at,
        )
        if not last_preparation_at:
            reason = "No previous successful session_preparation was found for this site."
        elif upload_needed:
            reason = "The current apply resume PDF is newer than the last successful session_preparation for this site."
        else:
            reason = "The current apply resume PDF is not newer than the last successful session_preparation for this site."
        return [
            {
                "role": "user",
                "content": (
                    "Resume freshness context for this session_preparation phase:\n"
                    f"- current_apply_resume_pdf_filename: {resume_pdf_path.name}\n"
                    f"- current_apply_resume_pdf_updated_at: {resume_updated_at or '(missing)'}\n"
                    f"- last_successful_session_preparation_at: {last_preparation_at or '(none)'}\n"
                    f"- resume_upload_needed: {'true' if upload_needed else 'false'}\n"
                    f"- reason: {reason}\n"
                    "- resume_filename_sync_key: current_apply_resume_pdf_filename is the preferred remote sync key when the site shows a resume filename.\n"
                    "resume_upload_needed is a local timestamp hint; it does not prove that the remote site already has the current PDF. "
                    "If the active site skill requires a remote resume filename check, compare the site's visible resume filename with current_apply_resume_pdf_filename and upload the current staged PDF when it differs, is missing, or cannot be confirmed. "
                    "If the site does not support remote filename checking and no live evidence shows a missing, mismatched, or unusable resume, then resume_upload_needed=false means do not reopen the resume manager just to upload again."
                ),
            }
        ]

    def _workflow_memory_context_items(self, *, site_key: str, phase_slug: str) -> list[dict[str, str]]:
        text = WorkflowMemoryStore(self.workspace).context_text(site_key=site_key, phase=phase_slug)
        if not text:
            return []
        return [{"role": "user", "content": text}]

    def _update_workflow_memory_from_phase_result(
        self,
        *,
        site_key: str,
        phase_slug: str,
        batch_id: str,
        turn_id: str,
        phase_result: BrowserPhaseResult,
    ) -> None:
        if str(phase_result.status or "").strip().lower() not in {"done", "waiting_user", "blocked", "failed"}:
            return
        try:
            WorkflowMemoryStore(self.workspace).update_phase(
                site_key=site_key,
                phase=phase_slug,
                status=phase_result.status,
                batch_id=batch_id,
                turn_id=turn_id,
                current_url=phase_result.current_url,
                trace_ref=phase_result.trace_ref,
                reason_tag=phase_result.reason_tag,
                summary=phase_result.summary,
                step_count=phase_result.step_count,
                recorded_count=phase_result.recorded_count,
                new_count=phase_result.new_count,
            )
        except Exception:
            return

    def _create_failed_phase_refinement_card(
        self,
        *,
        site_key: str,
        site_name: str,
        phase_slug: str,
        batch_id: str,
        phase_result: BrowserPhaseResult,
    ) -> dict[str, Any]:
        workflow_memory_path = WorkflowMemoryStore(self.workspace).path(site_key)
        failure_snapshot = extract_failure_snapshot_from_trace(
            workspace=self.workspace,
            site_key=site_key,
            batch_id=batch_id,
            phase=phase_slug,
            trace_ref=phase_result.trace_ref,
        )
        try:
            return create_site_skill_refinement_card(
                workspace=self.workspace,
                project_root=self.project_root,
                site_key=site_key,
                site_name=site_name,
                phase=phase_slug,
                batch_id=batch_id,
                reason_tag=phase_result.reason_tag,
                summary=phase_result.summary,
                current_url=phase_result.current_url,
                trace_ref=phase_result.trace_ref,
                skill_path=self._site_skill_path(site_key),
                workflow_memory_path=workflow_memory_path,
                failure_snapshot_path=failure_snapshot,
            )
        except Exception:
            return {}

    def _current_apply_resume_pdf_updated_at(self, resume_pdf_path: Path) -> str:
        path = Path(resume_pdf_path)
        if not path.is_file():
            return ""
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")

    def _last_successful_session_preparation_at(self, site_key: str) -> str:
        path = self.site_store.site_dir(site_key) / "events" / "all.jsonl"
        if not path.exists():
            return ""
        for row in JSONLStore(path).iter_rows_reverse():
            if str(row.get("name") or "") != "browser.phase.done":
                continue
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            if str(payload.get("phase") or "") != "session_preparation":
                continue
            ts = str(row.get("ts") or "")
            if ts:
                return ts
        return ""

    @classmethod
    def _resume_upload_needed(cls, *, resume_updated_at: str, last_preparation_at: str) -> bool:
        if not resume_updated_at:
            return False
        if not last_preparation_at:
            return True
        resume_dt = cls._parse_iso_timestamp(resume_updated_at)
        preparation_dt = cls._parse_iso_timestamp(last_preparation_at)
        if resume_dt is None or preparation_dt is None:
            return resume_updated_at > last_preparation_at
        try:
            return resume_dt > preparation_dt
        except TypeError:
            return resume_updated_at > last_preparation_at

    @staticmethod
    def _parse_iso_timestamp(value: str) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _stage_apply_resume_pdf(self, *, runtime_output_dir: Path) -> Path:
        source_path = default_apply_resume_pdf_path(self.workspace)
        if not source_path.is_file():
            raise FileNotFoundError(f"resume source not found: {source_path}")
        runtime_output_dir.mkdir(parents=True, exist_ok=True)
        staged_path = runtime_output_dir / source_path.name
        if source_path.resolve() != staged_path.resolve():
            shutil.copy2(source_path, staged_path)
        elif not staged_path.exists():
            shutil.copy2(source_path, staged_path)
        return staged_path

    def _phase_step_timeout_seconds(self, phase_slug: str) -> int:
        base_timeout = int(self.budgets.step_timeout_seconds or self._phase_runtime_settings["step_timeout_seconds"] or 30)
        normalized_phase = str(phase_slug or "").strip()
        if normalized_phase == "job_retrieval":
            return max(base_timeout, int(self.budgets.job_retrieval_step_timeout_seconds or base_timeout))
        if normalized_phase == "apply":
            return max(base_timeout, int(self.budgets.apply_step_timeout_seconds or base_timeout))
        return base_timeout

    def _persist_apply_carry_forward(
        self,
        *,
        site_key: str,
        batch_id: str,
        phase_memory: BrowserPhaseMemory | None,
    ) -> None:
        if not batch_id or not isinstance(phase_memory, BrowserPhaseMemory):
            return
        carry_forward = phase_memory.get_text("apply_carry_forward")
        if not carry_forward:
            return
        save_run_context = getattr(self.site_store, "save_run_context", None)
        if not callable(save_run_context):
            return
        save_run_context(
            site_key,
            batch_id,
            {
                "apply_carry_forward": carry_forward,
                "apply_carry_forward_updated_at": now_iso(),
            },
        )

    def _create_phase_runtime(self, phase_slug: str = "") -> Any:
        override = self.phase_runtime
        if override is not None:
            return override
        step_timeout_seconds = self._phase_step_timeout_seconds(phase_slug)
        if not callable(self._phase_runtime_factory):
            raise RuntimeError("browser phase runtime factory is not configured")
        settings = dict(self._phase_runtime_settings)
        settings["step_timeout_seconds"] = step_timeout_seconds
        return self._phase_runtime_factory(settings)

    def _profile_owner_lock_path(self, site_key: str) -> Path:
        return self._runtime_registry.profile_lock_path(self.site_store.browser_profile_dir(site_key))

    def _reserve_runtime(self, site_key: str, entry_url: str, timeout_ms: int | None = None) -> tuple[BrowserRuntimeLease, bool]:
        return self._runtime_registry.reserve(
            site_key=site_key,
            entry_url=entry_url,
            profile_dir=self.site_store.browser_profile_dir(site_key),
            timeout_ms=timeout_ms,
        )

    def _release_runtime(self, site_key: str) -> bool:
        released = self._runtime_registry.release_or_reclaim(
            site_key=site_key,
            profile_dir=self.site_store.browser_profile_dir(site_key),
        )
        self.site_store.save_browser_session(site_key, {"browser_status": "stopped", "active_run_id": ""})
        return released

    async def _run_site_async(
        self,
        *,
        site_key: str,
        site_name: str,
        entry_url: str,
        session_id: str,
        turn_id: str,
        batch_id: str,
        resume: bool,
        phase_slugs: tuple[str, ...] | None,
        apply_target_job_ids: tuple[str, ...] | None = None,
        continuation_context: dict[str, Any] | None = None,
        phase_timeout_seconds_override: int | None = None,
        timeout_ms_override: int | None = None,
    ) -> BrowserAutomationResult:
        active, reused_runtime = self._reserve_runtime(site_key, entry_url, timeout_ms=timeout_ms_override)
        self.site_store.save_browser_session(
            site_key,
            {
                "browser_status": "running",
                "last_browser_pid": active.runtime.pid(),
                "last_browser_opened_at": now_iso(),
                "active_run_id": turn_id,
                "last_known_url": entry_url or "",
                "current_trace_ref": "",
                "pending_action": "",
                "current_step_id": "",
                "current_step_attempt": 0,
                "current_step_status": "",
                "expected_outcome": "",
                "last_step_error": "",
                "mcp_log_path": str(active.runtime.log_path),
            },
        )
        self.site_store.append_event(
            site_key,
            "browser.runtime.started",
            {
                "turn_id": turn_id,
                "log_path": str(active.runtime.log_path),
                "pid": int(active.runtime.pid()),
                "run_id": active.runtime.run_id,
            },
        )

        effective_timeout_ms = int(timeout_ms_override or self.timeout_ms or 45000)
        bridge = MCPToolBridge(active.runtime, timeout_seconds=max(30.0, effective_timeout_ms / 1000.0))
        keep_runtime = False
        result: BrowserAutomationResult | None = None
        allowed_slugs = set(phase_slugs or DEFAULT_RUN_PHASES)
        phases = self._phase_prompts(site_key, allowed_slugs=allowed_slugs)
        if not phases:
            result = BrowserAutomationResult(
                site_key=site_key,
                site_name=site_name,
                status="failed",
                reason_tag="phase_missing",
                message="site skill has no supported phases",
            )
        else:
            try:
                await bridge.wait_until_ready(
                    seconds=max(5.0, min(30.0, float(effective_timeout_ms) / 1000.0)),
                    poll_interval=0.25,
                )
            except Exception as exc:
                result = BrowserAutomationResult(
                    site_key=site_key,
                    site_name=site_name,
                    status="failed",
                    reason_tag="mcp_not_ready",
                    message=f"{exc} ({active.runtime.log_path})",
                )
            else:
                session_state = self.site_store.ensure_browser_session(site_key)
                apply_staged_resume_pdf_path = ""
                if result is None:
                    try:
                        staged_resume_pdf = self._stage_apply_resume_pdf(runtime_output_dir=active.runtime.output_dir)
                    except Exception as exc:
                        if any(phase.slug == "apply" for phase in phases):
                            result = BrowserAutomationResult(
                                site_key=site_key,
                                site_name=site_name,
                                status="failed",
                                reason_tag="resume_pdf_unavailable",
                                message=str(exc),
                            )
                    else:
                        apply_staged_resume_pdf_path = str(staged_resume_pdf)
                        self.site_store.save_browser_session(
                            site_key,
                            {"staged_resume_pdf_path": apply_staged_resume_pdf_path},
                        )
                resume_phase = str(session_state.get("resume_phase") or "").strip()
                start_index = 0
                if resume and resume_phase:
                    for idx, phase in enumerate(phases):
                        if phase.slug == resume_phase:
                            start_index = idx
                            break
                phases = phases[start_index:]

                async with bridge.open_session() as mcp_session:
                    tools = await bridge.list_tools(mcp_session)
                    all_tool_names = {str(getattr(tool, "name", "") or "").strip() for tool in tools}

                    target_url = ""
                    if resume:
                        target_url = str(session_state.get("last_known_url") or entry_url or active.entry_url or "")
                    else:
                        target_url = entry_url or active.entry_url
                    should_navigate = bool(
                        target_url and "browser_navigate" in all_tool_names and (not resume or not reused_runtime)
                    )
                    if should_navigate:
                        payload = await bridge.call_tool(mcp_session, "browser_navigate", {"url": target_url})
                        current_url = bridge.extract_current_url(payload) or target_url
                        trace_ref = self.site_store.append_step_trace(
                            site_key,
                            turn_id,
                            {
                                "phase": "bootstrap",
                                "step_id": "bootstrap:browser_navigate",
                                "attempt": 1,
                                "tool_name": "browser_navigate",
                                "arguments": {"url": target_url},
                                "result": "error" if payload.get("isError") else "ok",
                                "output": bridge.summarize_tool_output(payload),
                            },
                        )
                        self.site_store.save_browser_session(
                            site_key,
                            {
                                "last_known_url": current_url,
                                "current_trace_ref": trace_ref,
                            },
                        )
                        if payload.get("isError"):
                            result = BrowserAutomationResult(
                                site_key=site_key,
                                site_name=site_name,
                                status="failed",
                                reason_tag="browser_navigate_failed",
                                message=bridge.summarize_tool_output(payload),
                                current_url=current_url,
                                trace_ref=trace_ref,
                            )

                    last_result: BrowserPhaseResult | None = None
                    current_url = str(session_state.get("last_known_url") or target_url)
                    phase_handoff = ""
                    previous_phase_memory: BrowserPhaseMemory | None = None
                    if result is None:
                        for phase in phases:
                            phase_runtime = self._create_phase_runtime(phase.slug)
                            response_tools, tool_names = self._response_tools_for_phase(tools, phase.slug)
                            phase_memory = BrowserPhaseMemory()
                            timeout_memory = previous_phase_memory if phase.slug == "job_retrieval" else None
                            phase_timeout_seconds = self._phase_timeout_seconds(phase.slug, phase_memory=timeout_memory)
                            self.site_store.append_event(
                                site_key,
                                "browser.phase.started",
                                {"turn_id": turn_id, "batch_id": batch_id, "phase": phase.slug},
                            )
                            extra_context_items = []
                            if phase.slug == "session_preparation":
                                extra_context_items.extend(self._session_preparation_context_items(site_key))
                            extra_context_items.extend(
                                self._workflow_memory_context_items(site_key=site_key, phase_slug=phase.slug)
                            )
                            override_seconds = int(phase_timeout_seconds_override or 0)
                            effective_phase_timeout_seconds = phase_timeout_seconds
                            if override_seconds > 0:
                                if phase.slug == "apply":
                                    effective_phase_timeout_seconds = override_seconds
                                else:
                                    effective_phase_timeout_seconds = max(phase_timeout_seconds, override_seconds)
                            try:
                                phase_result = await phase_runtime.run_phase(
                                    site_key=site_key,
                                    site_name=site_name,
                                    entry_url=entry_url or active.entry_url,
                                    phase=phase,
                                    bridge=bridge,
                                    session=mcp_session,
                                    site_store=self.site_store,
                                    session_id=session_id,
                                    turn_id=turn_id,
                                    batch_id=batch_id,
                                    response_tools=response_tools,
                                    tool_names=tool_names,
                                    phase_handoff=phase_handoff,
                                    phase_timeout_seconds=effective_phase_timeout_seconds,
                                    max_phase_steps=self._phase_max_steps(phase.slug),
                                    extra_context_items=extra_context_items,
                                context_session=self._phase_context_session(
                                        site_key=site_key,
                                        phase_slug=phase.slug,
                                        batch_id=batch_id,
                                        target_job_ids=apply_target_job_ids,
                                        staged_resume_pdf_path=apply_staged_resume_pdf_path,
                                        phase_memory=phase_memory,
                                    continuation_context=continuation_context,
                                ),
                                phase_context=build_phase_context(
                                    phase,
                                    phase_memory=phase_memory,
                                    continuation=continuation_context,
                                    local_state={
                                        "site_key": site_key,
                                        "batch_id": batch_id,
                                        "session_id": session_id,
                                        "turn_id": turn_id,
                                        "apply_target_job_ids": list(apply_target_job_ids or ()),
                                        "cache_dependency_versions": self._cache_dependency_versions(site_key),
                                    },
                                    cache_candidates=self._cache_candidates(
                                        site_key=site_key,
                                        phase_slug=phase.slug,
                                        batch_id=batch_id,
                                    ),
                                ),
                                apply_staged_resume_pdf_path=apply_staged_resume_pdf_path,
                                )
                            finally:
                                if phase_runtime is not self.phase_runtime:
                                    await self._aclose_phase_runtime(phase_runtime)
                            last_result = phase_result
                            current_url = str(phase_result.current_url or current_url or target_url)
                            self._update_workflow_memory_from_phase_result(
                                site_key=site_key,
                                phase_slug=phase.slug,
                                batch_id=batch_id,
                                turn_id=turn_id,
                                phase_result=phase_result,
                            )
                            if phase_result.status == "done":
                                if phase.slug == "apply":
                                    self._persist_apply_carry_forward(
                                        site_key=site_key,
                                        batch_id=batch_id,
                                        phase_memory=phase_memory,
                                    )
                                previous_phase_memory = phase_memory
                                phase_handoff = f"{phase.title} completed. {phase_result.summary.strip()[:300]}".strip()
                                authenticated_ready = bool(session_state.get("authenticated_ready") or session_state.get("session_ready"))
                                jobs_surface_ready = bool(session_state.get("jobs_surface_ready"))
                                if phase.slug == "session_preparation":
                                    authenticated_ready = True
                                if self._phase_has_jobs_surface(phase.slug):
                                    jobs_surface_ready = True
                                self.site_store.save_browser_session(
                                    site_key,
                                    {
                                        "last_phase_slug": phase.slug,
                                        "last_phase_status": phase_result.status,
                                        "last_phase_summary": phase_result.summary[:500],
                                        "resume_phase": "",
                                        "pending_action": "",
                                        "session_ready": authenticated_ready,
                                        "authenticated_ready": authenticated_ready,
                                        "jobs_surface_ready": jobs_surface_ready,
                                        "last_known_url": current_url,
                                        "current_trace_ref": phase_result.trace_ref,
                                    },
                                )
                                self.site_store.append_event(
                                    site_key,
                                    "browser.phase.done",
                                    {
                                        "turn_id": turn_id,
                                        "batch_id": batch_id,
                                        "phase": phase.slug,
                                        "summary": phase_result.summary,
                                        "step_count": phase_result.step_count,
                                        "recorded_count": phase_result.recorded_count,
                                        "new_count": phase_result.new_count,
                                        "trace_ref": phase_result.trace_ref,
                                    },
                                )
                                session_state = self.site_store.ensure_browser_session(site_key)
                                continue
                            if phase_result.status == "blocked":
                                message = phase_result.summary.strip() or f"{site_key} 需要先完成登录，关闭窗口后再回复 `{site_key} done`。"
                                self.site_store.save_browser_session(
                                    site_key,
                                    {
                                        "last_phase_slug": phase.slug,
                                        "last_phase_status": phase_result.status,
                                        "last_phase_summary": phase_result.summary[:500],
                                        "resume_phase": phase.slug,
                                        "pending_action": "waiting_user",
                                        "session_ready": False,
                                        "authenticated_ready": False,
                                        "jobs_surface_ready": bool(session_state.get("jobs_surface_ready")),
                                        "last_known_url": current_url,
                                        "current_trace_ref": phase_result.trace_ref,
                                        "browser_status": "waiting_user",
                                    },
                                )
                                self.site_store.append_event(
                                    site_key,
                                    "browser.phase.blocked",
                                    {
                                        "turn_id": turn_id,
                                        "batch_id": batch_id,
                                        "phase": phase.slug,
                                        "summary": phase_result.summary,
                                        "reason_tag": phase_result.reason_tag,
                                        "step_count": phase_result.step_count,
                                        "trace_ref": phase_result.trace_ref,
                                    },
                                )
                                keep_runtime = True
                                result = BrowserAutomationResult(
                                    site_key=site_key,
                                    site_name=site_name,
                                    status="blocked",
                                    reason_tag=phase_result.reason_tag,
                                    message=message,
                                    current_phase=phase.slug,
                                    current_url=current_url,
                                    trace_ref=phase_result.trace_ref,
                                    step_count=phase_result.step_count,
                                    retrieved_count=phase_result.recorded_count,
                                    new_job_count=phase_result.new_count,
                                )
                                break
                            self.site_store.append_event(
                                site_key,
                                "browser.phase.failed",
                                {
                                    "turn_id": turn_id,
                                    "batch_id": batch_id,
                                    "phase": phase.slug,
                                    "summary": phase_result.summary,
                                    "reason_tag": phase_result.reason_tag,
                                    "step_count": phase_result.step_count,
                                    "recorded_count": phase_result.recorded_count,
                                    "new_count": phase_result.new_count,
                                    "trace_ref": phase_result.trace_ref,
                                },
                            )
                            refinement_card = self._create_failed_phase_refinement_card(
                                site_key=site_key,
                                site_name=site_name,
                                phase_slug=phase.slug,
                                batch_id=batch_id,
                                phase_result=phase_result,
                            )
                            if refinement_card.get("card_id"):
                                self.site_store.append_event(
                                    site_key,
                                    "browser.phase.refinement_card",
                                    {
                                        "turn_id": turn_id,
                                        "batch_id": batch_id,
                                        "phase": phase.slug,
                                        "reason_tag": phase_result.reason_tag,
                                        "action_card_id": refinement_card.get("card_id") or "",
                                        "action_card_path": refinement_card.get("markdown_path") or "",
                                    },
                                )
                            result = BrowserAutomationResult(
                                site_key=site_key,
                                site_name=site_name,
                                status="failed",
                                reason_tag=phase_result.reason_tag,
                                message=phase_result.summary,
                                current_phase=phase.slug,
                                current_url=current_url,
                                trace_ref=phase_result.trace_ref,
                                step_count=phase_result.step_count,
                                retrieved_count=phase_result.recorded_count,
                                new_job_count=phase_result.new_count,
                            )
                            break

                    if result is None:
                        if last_result is None:
                            result = BrowserAutomationResult(
                                site_key=site_key,
                                site_name=site_name,
                                status="failed",
                                reason_tag="phase_missing",
                                message="no phases executed",
                            )
                        else:
                            keep_runtime = self.keep_open
                            authenticated_ready = bool(session_state.get("authenticated_ready") or session_state.get("session_ready"))
                            jobs_surface_ready = bool(session_state.get("jobs_surface_ready")) or self._phase_has_jobs_surface(
                                phases[-1].slug if phases else ""
                            )
                            self.site_store.save_browser_session(
                                site_key,
                                {
                                    "browser_status": "ready" if keep_runtime else "running",
                                    "pending_action": "",
                                    "resume_phase": "",
                                    "session_ready": authenticated_ready,
                                    "authenticated_ready": authenticated_ready,
                                    "jobs_surface_ready": jobs_surface_ready,
                                    "last_known_url": str(last_result.current_url or current_url or target_url),
                                    "current_trace_ref": last_result.trace_ref,
                                },
                            )
                            result = BrowserAutomationResult(
                                site_key=site_key,
                                site_name=site_name,
                                status="ready",
                                reason_tag="ready",
                                message=self._ready_message_for_phase(
                                    phases[-1].slug if phases else "",
                                    authenticated_ready=authenticated_ready,
                                    jobs_surface_ready=jobs_surface_ready,
                                ),
                                current_phase=phases[-1].slug if phases else "",
                                current_url=str(last_result.current_url or current_url or target_url),
                                trace_ref=last_result.trace_ref,
                                step_count=last_result.step_count,
                                retrieved_count=last_result.recorded_count,
                                new_job_count=last_result.new_count,
                                authenticated_ready=authenticated_ready,
                                jobs_surface_ready=jobs_surface_ready,
                            )

        if result is None:
            raise RuntimeError(f"site {site_key} finished without a result")
        if not keep_runtime:
            self._release_runtime(site_key)
        return result

    def run_site(
        self,
        *,
        site_key: str,
        site_name: str,
        entry_url: str,
        session_id: str,
        turn_id: str,
        batch_id: str,
        resume: bool = False,
        phase_slugs: tuple[str, ...] | None = None,
        apply_target_job_ids: tuple[str, ...] | None = None,
        continuation_context: dict[str, Any] | None = None,
        phase_timeout_seconds_override: int | None = None,
        timeout_ms_override: int | None = None,
    ) -> BrowserAutomationResult:
        if self.execution_mode in {AGENT_BRIDGE_MODE, CODEX_APP_SERVER_MODE}:
            return self._run_site_agent_bridge(
                site_key=site_key,
                site_name=site_name,
                entry_url=entry_url,
                session_id=session_id,
                turn_id=turn_id,
                batch_id=batch_id,
                resume=resume,
                phase_slugs=phase_slugs,
                apply_target_job_ids=apply_target_job_ids,
                continuation_context=continuation_context,
            )
        try:
            async def _runner() -> BrowserAutomationResult:
                return await self._run_site_async(
                    site_key=site_key,
                    site_name=site_name,
                    entry_url=entry_url,
                    session_id=session_id,
                    turn_id=turn_id,
                    batch_id=batch_id,
                    resume=resume,
                    phase_slugs=phase_slugs,
                    apply_target_job_ids=apply_target_job_ids,
                    continuation_context=continuation_context,
                    phase_timeout_seconds_override=phase_timeout_seconds_override,
                    timeout_ms_override=timeout_ms_override,
                )

            return anyio.run(_runner)
        except BrowserProfileOwnerError as exc:
            message = MCPToolBridge._format_exception(exc) or str(exc)
            self.site_store.save_browser_session(
                site_key,
                {
                    "browser_status": "profile_in_use",
                    "last_step_error": message[:1000],
                },
            )
            return BrowserAutomationResult(
                site_key=site_key,
                site_name=site_name,
                status="failed",
                reason_tag="browser_profile_in_use",
                message=message[:4000],
            )
        except Exception as exc:
            self._release_runtime(site_key)
            message = MCPToolBridge._format_exception(exc) or str(exc)
            return BrowserAutomationResult(
                site_key=site_key,
                site_name=site_name,
                status="failed",
                reason_tag="browser_runtime_failed",
                message=message[:4000],
            )

    def finish_site(self, site_key: str) -> bool:
        released = self._release_runtime(site_key)
        normalized_site = str(site_key or "").strip()
        self._context_resource_scopes = {
            scope for scope in self._context_resource_scopes if scope[0] != normalized_site
        }
        if not self._context_resource_scopes:
            self._browser_context_registry.release_loaded_bundles()
        return released

    def _retain_context_resource_scope(self, *, site_key: str, batch_id: str) -> None:
        normalized_site = str(site_key or "").strip()
        normalized_batch = str(batch_id or "").strip()
        if normalized_site and normalized_batch:
            self._context_resource_scopes.add((normalized_site, normalized_batch))
