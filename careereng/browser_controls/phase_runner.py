"""Site-level browser orchestration for browser-driven job phases."""

from __future__ import annotations

from dataclasses import dataclass
import json
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
from careereng.resume.export import default_apply_resume_pdf_path
from careereng.utils import now_iso


SUPPORTED_PHASES = {"session_preparation", "channel_discovery", "job_filtering", "job_retrieval", "apply"}
DEFAULT_RUN_PHASES = ("session_preparation", "channel_discovery", "job_filtering", "job_retrieval")
GLOBAL_BLOCKED_TOOL_NAMES = {"browser_run_code"}
SESSION_PREPARATION_BLOCKED_TOOL_NAMES = {"browser_resize"}
JOB_RETRIEVAL_BLOCKED_TOOL_NAMES = {"browser_navigate"}
JOB_FILTERING_PHASE_TIMEOUT_SECONDS = 420
JOB_RETRIEVAL_PHASE_TIMEOUT_SECONDS = 900
JOB_RETRIEVAL_MAX_PHASE_STEPS = 96
APPLY_PHASE_TIMEOUT_SECONDS = 3600
APPLY_PHASE_MAX_PHASE_STEPS = 240


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
    ):
        self.project_root = Path(project_root).resolve()
        self.workspace = Path(workspace).resolve()
        self.site_store = site_store
        self.headless = bool(headless)
        self.keep_open = bool(keep_open)
        self.timeout_ms = int(timeout_ms or 45000)
        self.browser_name = browser_name or "chrome"
        self.phase_timeout_seconds = int(phase_timeout_seconds or 180)
        self.max_phase_steps = int(max_phase_steps or 24)
        self._lock = threading.Lock()
        self._active: dict[str, ActiveSiteRuntime] = {}
        self._phase_runtime_config = BrowserRuntimeConfig(
            api_base=api_base,
            api_key=api_key,
            model=model,
            reasoning_effort=reasoning_effort,
            phase_timeout_seconds=self.phase_timeout_seconds,
            step_timeout_seconds=step_timeout_seconds,
            max_step_retries=max_step_retries,
            max_phase_steps=self.max_phase_steps,
        )
        self.phase_runtime: BrowserPhaseRuntime | Any | None = None

    def close(self) -> None:
        with self._lock:
            active = list(self._active.values())
            self._active.clear()
        for item in active:
            item.runtime.stop()
            self.site_store.save_browser_session(item.site_key, {"browser_status": "stopped", "active_run_id": ""})

    def _project_skill_path(self) -> Path:
        return self.project_root / "skills" / "search" / "jobs" / "SKILL.md"

    def _site_skill_path(self, site_key: str) -> Path:
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

    def _phase_timeout_seconds(self, phase_slug: str) -> int:
        base = int(self.phase_timeout_seconds or 180)
        if str(phase_slug or "").strip() == "job_retrieval":
            return max(base, JOB_RETRIEVAL_PHASE_TIMEOUT_SECONDS)
        if str(phase_slug or "").strip() == "apply":
            return max(base, APPLY_PHASE_TIMEOUT_SECONDS)
        if str(phase_slug or "").strip() == "job_filtering":
            return max(base, JOB_FILTERING_PHASE_TIMEOUT_SECONDS)
        return base

    def _phase_max_steps(self, phase_slug: str) -> int:
        base = int(self.max_phase_steps or 24)
        if str(phase_slug or "").strip() == "job_retrieval":
            return max(base, JOB_RETRIEVAL_MAX_PHASE_STEPS)
        if str(phase_slug or "").strip() == "apply":
            return max(base, APPLY_PHASE_MAX_PHASE_STEPS)
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
        if phase_slug == "job_retrieval":
            schema = BrowserPhaseRuntime.record_jobs_tool()
            response_tools.append(schema)
            tool_names.add(str(schema.get("name") or "record_jobs"))
        if phase_slug == "apply":
            schema = BrowserPhaseRuntime.update_jobs_tool()
            response_tools.append(schema)
            tool_names.add(str(schema.get("name") or "update_jobs"))
        return response_tools, tool_names

    def _apply_context_items(
        self,
        *,
        site_key: str,
        batch_id: str,
        target_job_ids: tuple[str, ...] | None = None,
        staged_resume_pdf_path: str = "",
    ) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        target_ids = {str(job_id or "").strip() for job_id in (target_job_ids or ()) if str(job_id or "").strip()}
        list_run_jobs = getattr(self.site_store, "list_run_jobs", None)
        if callable(list_run_jobs):
            try:
                run_rows = list_run_jobs(site_key, batch_id)
            except Exception:
                run_rows = []
        else:
            run_rows = []
        terminal_decisions = {"filtered_out", "already_applied"}
        terminal_applications = {"already_applied", "submitted", "apply_failed", "blocked"}
        pending_rows: list[dict[str, Any]] = []
        for row in run_rows:
            if not isinstance(row, dict):
                continue
            job_id = str(row.get("job_id") or "").strip()
            if target_ids and job_id not in target_ids:
                continue
            decision_status = str(row.get("decision_status") or "").strip().lower()
            application_status = str(row.get("application_status") or "").strip().lower()
            if decision_status in terminal_decisions or application_status in terminal_applications:
                continue
            pending_rows.append(
                {
                    "job_id": job_id,
                    "title": str(row.get("title") or ""),
                    "url": str(row.get("url") or ""),
                    "location": str(row.get("location") or ""),
                    "posted_label": str(row.get("posted_label") or ""),
                    "employment_type": str(row.get("employment_type") or ""),
                    "match_label": str(row.get("match_label") or ""),
                    "apply_state": str(row.get("apply_state") or ""),
                    "decision_status": str(row.get("decision_status") or ""),
                    "application_status": str(row.get("application_status") or ""),
                }
            )
        items.append(
            {
                "role": "user",
                "content": (
                    (
                        "Current apply target for this site and batch. Work only on this job:\n"
                        if len(target_ids) == 1
                        else "Current apply targets for this site and batch:\n"
                    )
                    + json.dumps(pending_rows, ensure_ascii=False)
                ),
            }
        )
        resume_pdf_path = str(staged_resume_pdf_path or "").strip() or str(default_apply_resume_pdf_path(self.workspace))
        items.append(
            {
                "role": "user",
                "content": (
                    "Run-local staged resume PDF for this apply phase "
                    f"(use this exact local path if upload is needed): {resume_pdf_path}"
                ),
            }
        )
        resume_file_name = Path(resume_pdf_path).name.strip()
        if resume_file_name:
            items.append(
                {
                    "role": "user",
                    "content": (
                        "Current staged resume filename for this apply phase "
                        f"(compare the live page's selected resume name against this basename): {resume_file_name}"
                    ),
                }
            )
        persona_path = self.workspace / "profile" / "persona.md"
        persona_text = load_text(persona_path).strip()
        if persona_text:
            items.append({"role": "user", "content": f"Current persona.md:\n{persona_text[:24000]}"})
        current_dir = self.workspace / "cv" / "current"
        cv_text = ""
        if current_dir.exists():
            for path in sorted(current_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
                if not path.is_file() or path.name == "metadata.json":
                    continue
                cv_text = load_text(path).strip()
                if cv_text:
                    break
        if cv_text:
            items.append({"role": "user", "content": f"Current CV text:\n{cv_text[:24000]}"})
        return items

    def _phase_context_items(
        self,
        *,
        site_key: str,
        phase_slug: str,
        batch_id: str,
        target_job_ids: tuple[str, ...] | None = None,
        staged_resume_pdf_path: str = "",
    ) -> list[dict[str, str]]:
        if phase_slug == "apply":
            return self._apply_context_items(
                site_key=site_key,
                batch_id=batch_id,
                target_job_ids=target_job_ids,
                staged_resume_pdf_path=staged_resume_pdf_path,
            )
        return []

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

    def _create_phase_runtime(self) -> BrowserPhaseRuntime | Any:
        override = self.phase_runtime
        if override is not None:
            return override
        return BrowserPhaseRuntime(self._phase_runtime_config)

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
        phase_runtime = self._create_phase_runtime()
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
                if result is None and any(phase.slug == "apply" for phase in phases):
                    try:
                        staged_resume_pdf = self._stage_apply_resume_pdf(runtime_output_dir=active.runtime.output_dir)
                    except Exception as exc:
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
                    if result is None:
                        for phase in phases:
                            response_tools, tool_names = self._response_tools_for_phase(tools, phase.slug)
                            self.site_store.append_event(site_key, "browser.phase.started", {"turn_id": turn_id, "phase": phase.slug})
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
                                phase_timeout_seconds=(
                                    max(self._phase_timeout_seconds(phase.slug), int(phase_timeout_seconds_override or 0))
                                    if phase.slug == "apply"
                                    else self._phase_timeout_seconds(phase.slug)
                                ),
                                max_phase_steps=self._phase_max_steps(phase.slug),
                                extra_context_items=self._phase_context_items(
                                    site_key=site_key,
                                    phase_slug=phase.slug,
                                    batch_id=batch_id,
                                    target_job_ids=apply_target_job_ids,
                                    staged_resume_pdf_path=apply_staged_resume_pdf_path,
                                ),
                                apply_staged_resume_pdf_path=apply_staged_resume_pdf_path,
                            )
                            last_result = phase_result
                            current_url = str(phase_result.current_url or current_url or target_url)
                            if phase_result.status == "done":
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
                                    {"turn_id": turn_id, "phase": phase.slug, "summary": phase_result.summary},
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
                                    {"turn_id": turn_id, "phase": phase.slug, "summary": phase_result.summary},
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
                                {"turn_id": turn_id, "phase": phase.slug, "summary": phase_result.summary, "reason_tag": phase_result.reason_tag},
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
