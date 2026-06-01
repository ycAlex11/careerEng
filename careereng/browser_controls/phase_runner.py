"""Site-level browser orchestration for browser-driven job phases."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
import shutil
import threading
from typing import Any
import anyio

from careereng.browser_controls.backends.playwright_mcp import (
    PlaywrightMCPProcess,
    launch_playwright_mcp,
    wait_for_process,
)
from careereng.browser_controls.bridge import MCPToolBridge
from careereng.browser_controls.prompting import build_phase_prompts, load_text
from careereng.browser_controls.runtime import BrowserPhaseResult, BrowserPhaseRuntime, BrowserRuntimeConfig
from careereng.browser_context import BrowserContextRegistry, BrowserContextSession, BrowserPhaseMemory
from careereng.config.schema import BrowserBudgetsConfig
from careereng.resume.export import default_apply_resume_pdf_path
from careereng.storage.jsonl import JSONLStore
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


@dataclass
class ActiveSiteRuntime:
    site_key: str
    runtime: PlaywrightMCPProcess
    entry_url: str


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
        budgets: BrowserBudgetsConfig | None = None,
    ):
        self.project_root = Path(project_root).resolve()
        self.workspace = Path(workspace).resolve()
        self.site_store = site_store
        self.headless = bool(headless)
        self.keep_open = bool(keep_open)
        self.timeout_ms = int(timeout_ms or 45000)
        self.browser_name = browser_name or "chrome"
        self.budgets = budgets or BrowserBudgetsConfig(
            phase_timeout_seconds=int(phase_timeout_seconds or 180),
            step_timeout_seconds=int(step_timeout_seconds or 30),
            max_step_retries=int(max_step_retries or 1),
            max_phase_steps=int(max_phase_steps or 24),
        )
        self.phase_timeout_seconds = int(self.budgets.phase_timeout_seconds or phase_timeout_seconds or 180)
        self.max_phase_steps = int(self.budgets.max_phase_steps or max_phase_steps or 24)
        self._lock = threading.Lock()
        self._active: dict[str, ActiveSiteRuntime] = {}
        self._browser_context_registry = BrowserContextRegistry(self.workspace)
        self._phase_runtime_config = BrowserRuntimeConfig(
            api_base=api_base,
            api_key=api_key,
            model=model,
            reasoning_effort=reasoning_effort,
            phase_timeout_seconds=self.phase_timeout_seconds,
            step_timeout_seconds=int(self.budgets.step_timeout_seconds or step_timeout_seconds or 30),
            max_step_retries=int(self.budgets.max_step_retries or max_step_retries or 1),
            max_phase_steps=self.max_phase_steps,
            metrics_workspace=str(self.workspace),
        )
        self.phase_runtime: BrowserPhaseRuntime | Any | None = None

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
        with self._lock:
            active = list(self._active.values())
            self._active.clear()
        for item in active:
            item.runtime.stop()
            self.site_store.save_browser_session(item.site_key, {"browser_status": "stopped", "active_run_id": ""})
        shared_phase_runtime = self.phase_runtime
        self.phase_runtime = None
        if shared_phase_runtime is not None:
            anyio.run(self._aclose_phase_runtime, shared_phase_runtime)

    def _project_skill_path(self) -> Path:
        return self.project_root / "skills" / "search" / "jobs" / "SKILL.md"

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
        schema = BrowserPhaseRuntime.update_phase_memory_tool()
        response_tools.append(schema)
        tool_names.add(str(schema.get("name") or "update_phase_memory"))
        if phase_slug == "application_status_review":
            schema = BrowserPhaseRuntime.record_application_reviews_tool()
            response_tools.append(schema)
            tool_names.add(str(schema.get("name") or "record_application_reviews"))
        if phase_slug == "job_retrieval":
            schema = BrowserPhaseRuntime.record_jobs_tool()
            response_tools.append(schema)
            tool_names.add(str(schema.get("name") or "record_jobs"))
        if phase_slug == "apply":
            schema = BrowserPhaseRuntime.update_jobs_tool()
            response_tools.append(schema)
            tool_names.add(str(schema.get("name") or "update_jobs"))
            schema = BrowserPhaseRuntime.request_context_tool()
            response_tools.append(schema)
            tool_names.add(str(schema.get("name") or "request_context"))
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
    ) -> BrowserContextSession | None:
        if phase_slug == "apply":
            self._browser_context_registry.refresh()
            return BrowserContextSession.for_apply(
                registry=self._browser_context_registry,
                workspace=self.workspace,
                site_store=self.site_store,
                site_key=site_key,
                batch_id=batch_id,
                target_job_ids=target_job_ids,
                staged_resume_pdf_path=staged_resume_pdf_path,
                phase_memory=phase_memory,
            )
        return BrowserContextSession.for_phase(phase_memory=phase_memory)

    def _session_preparation_context_items(self, site_key: str) -> list[dict[str, str]]:
        resume_updated_at = self._latest_current_resume_markdown_updated_at()
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
            reason = "The current Markdown resume is newer than the last successful session_preparation for this site."
        else:
            reason = "The current Markdown resume is not newer than the last successful session_preparation for this site."
        return [
            {
                "role": "user",
                "content": (
                    "Resume freshness context for this session_preparation phase:\n"
                    f"- current_resume_markdown_updated_at: {resume_updated_at or '(missing)'}\n"
                    f"- last_successful_session_preparation_at: {last_preparation_at or '(none)'}\n"
                    f"- resume_upload_needed: {'true' if upload_needed else 'false'}\n"
                    f"- reason: {reason}\n"
                    "If resume_upload_needed is false, do not reopen the site's resume manager just to upload again. "
                    "Only repair the remote resume if the live site clearly shows that the resume is missing, mismatched, or unusable."
                ),
            }
        ]

    def _latest_current_resume_markdown_updated_at(self) -> str:
        current_dir = self.workspace / "cv" / "current"
        if not current_dir.exists():
            return ""
        candidates = [path for path in current_dir.glob("*.md") if path.is_file()]
        if not candidates:
            return ""
        latest = max(candidates, key=lambda path: path.stat().st_mtime)
        return datetime.fromtimestamp(latest.stat().st_mtime).isoformat(timespec="seconds")

    def _last_successful_session_preparation_at(self, site_key: str) -> str:
        path = self.site_store.site_dir(site_key) / "events" / "all.jsonl"
        if not path.exists():
            return ""
        latest = ""
        for row in JSONLStore(path).read_all():
            if str(row.get("name") or "") != "browser.phase.done":
                continue
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            if str(payload.get("phase") or "") != "session_preparation":
                continue
            ts = str(row.get("ts") or "")
            if ts and ts > latest:
                latest = ts
        return latest

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
        base_timeout = int(self.budgets.step_timeout_seconds or self._phase_runtime_config.step_timeout_seconds or 30)
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

    def _create_phase_runtime(self, phase_slug: str = "") -> BrowserPhaseRuntime | Any:
        override = self.phase_runtime
        if override is not None:
            return override
        step_timeout_seconds = self._phase_step_timeout_seconds(phase_slug)
        config = self._phase_runtime_config
        if step_timeout_seconds != int(config.step_timeout_seconds or 30):
            config = replace(config, step_timeout_seconds=step_timeout_seconds)
        return BrowserPhaseRuntime(config)

    def _reserve_runtime(self, site_key: str, entry_url: str, timeout_ms: int | None = None) -> tuple[ActiveSiteRuntime, bool]:
        effective_timeout_ms = int(timeout_ms or self.timeout_ms or 45000)
        with self._lock:
            current = self._active.get(site_key)
            if current and current.runtime.is_running():
                current.entry_url = entry_url or current.entry_url
                current.runtime.command_timeout_seconds = max(
                    float(getattr(current.runtime, "command_timeout_seconds", 0.0) or 0.0),
                    max(45.0, float(effective_timeout_ms) / 1000.0 + 30.0),
                )
                return current, True
            if current:
                current.runtime.stop()
            run_id = now_iso().replace(":", "").replace("-", "")
            output_dir = self.workspace / "tmp" / "browser_controls" / site_key / run_id
            try:
                runtime = launch_playwright_mcp(
                    site_key=site_key,
                    run_id=run_id,
                    browser_name=self.browser_name,
                    headless=self.headless,
                    profile_dir=self.site_store.browser_profile_dir(site_key),
                    output_dir=output_dir,
                    timeout_ms=effective_timeout_ms,
                )
            except Exception:
                raise
            active = ActiveSiteRuntime(site_key=site_key, runtime=runtime, entry_url=entry_url)
            self._active[site_key] = active
            return active, False

    def _release_runtime(self, site_key: str) -> None:
        with self._lock:
            active = self._active.pop(site_key, None)
        if active:
            active.runtime.stop()
        self.site_store.save_browser_session(site_key, {"browser_status": "stopped", "active_run_id": ""})

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
        phase_timeout_seconds_override: int | None = None,
        timeout_ms_override: int | None = None,
    ) -> BrowserAutomationResult:
        active, reused_runtime = self._reserve_runtime(site_key, entry_url, timeout_ms=timeout_ms_override)
        wait_for_process(active.runtime)
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
                                    ),
                                    apply_staged_resume_pdf_path=apply_staged_resume_pdf_path,
                                )
                            finally:
                                if phase_runtime is not self.phase_runtime:
                                    await self._aclose_phase_runtime(phase_runtime)
                            last_result = phase_result
                            current_url = str(phase_result.current_url or current_url or target_url)
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
        phase_timeout_seconds_override: int | None = None,
        timeout_ms_override: int | None = None,
    ) -> BrowserAutomationResult:
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
                    phase_timeout_seconds_override=phase_timeout_seconds_override,
                    timeout_ms_override=timeout_ms_override,
                )

            return anyio.run(_runner)
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

    def finish_site(self, site_key: str) -> None:
        self._release_runtime(site_key)
