"""Shared runtime builders for CLI and workspace manager."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.career.applications.channel_locator import ChannelLocator
from careereng.orchestration.engine.agent_loop import AgentLoop
from careereng.orchestration.engine.browser_automation import BrowserAutomationService
from careereng.config.execution import CODEX_BACKEND, execution_backend_from_mode, resolve_execution_backend
from careereng.config.loader import load_auth, load_config
from careereng.adapters.providers import create_provider
from careereng.orchestration.agent_protocol.llm import ProviderError, StructuredOutputResult
from careereng.adapters.providers.browser_phase_runtime import BrowserPhaseRuntime, BrowserRuntimeConfig
from careereng.career.applications.search_store import SearchStore
from careereng.career.applications.site_store import SiteStore
from careereng.career.applications.site_tools import SiteTools


class FallbackProvider:
    def __init__(self, error: str):
        self.error = error

    def chat(self, messages, *, model):
        return f"Provider not configured: {self.error}"

    def chat_json(self, messages, *, model, schema=None, schema_name="response", json_mode="auto"):
        return StructuredOutputResult(
            data={},
            raw=f"Provider not configured: {self.error}",
            mode="error",
            used_fallback=True,
        )


class DisabledProvider(FallbackProvider):
    """Prevent an API transport from being used when config disables it."""

    def __init__(self):
        super().__init__("provider execution is disabled by config")


def _create_responses_browser_phase_runtime(settings: dict[str, Any]) -> BrowserPhaseRuntime:
    return BrowserPhaseRuntime(BrowserRuntimeConfig(**settings))


def project_root_from_cwd(cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    if (base / "pyproject.toml").exists() and (base / "careereng").exists():
        return base
    return Path(__file__).resolve().parents[2]


def workspace_path(project_root: Path) -> Path:
    config = load_config(project_root)
    path = config.paths.workspace_path(project_root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_browser_api_base(config: Any) -> str:
    provider_base = str(getattr(config.providers.openai, "api_base", "") or "").strip()
    return provider_base or "https://api.openai.com/v1"


def build_site_services(
    *,
    project_root: Path,
    workspace: Path | None = None,
) -> tuple[Path, Path, Any, SearchStore, SiteStore, SiteTools, ChannelLocator]:
    config = load_config(project_root)
    resolved_workspace = workspace or config.paths.workspace_path(project_root)
    resolved_workspace.mkdir(parents=True, exist_ok=True)
    site_store = SiteStore(resolved_workspace, project_root=project_root)
    search_store = SearchStore(resolved_workspace)
    site_tools = SiteTools(site_store)
    site_tools.project_root = project_root
    locator = ChannelLocator(site_tools=site_tools, search_store=search_store)
    return project_root, resolved_workspace, config, search_store, site_store, site_tools, locator


def build_loop(*, project_root: Path, workspace: Path | None = None) -> tuple[AgentLoop, Any]:
    config = load_config(project_root)
    auth = load_auth(project_root)
    resolved_workspace = workspace or config.paths.workspace_path(project_root)
    resolved_workspace.mkdir(parents=True, exist_ok=True)
    execution_backend, execution_error = resolve_execution_backend(config)
    if execution_error:
        raise ValueError(execution_error)
    if bool(getattr(config.execution, "provider_enabled", True)):
        try:
            _, provider = create_provider(config, auth, workspace=resolved_workspace)
        except ProviderError as exc:
            provider = FallbackProvider(str(exc))
    else:
        provider = DisabledProvider()

    configured_mode = str(config.browser.execution_mode or "provider")
    if execution_backend == CODEX_BACKEND:
        browser_execution_mode = (
            configured_mode
            if execution_backend_from_mode(configured_mode) == CODEX_BACKEND
            else "codex_app_server"
        )
    else:
        browser_execution_mode = "provider"

    site_store = SiteStore(resolved_workspace, project_root=project_root)
    site_tools = SiteTools(site_store)
    site_tools.project_root = project_root
    browser_runner = BrowserAutomationService(
        project_root=project_root,
        workspace=resolved_workspace,
        site_store=site_store,
        execution_mode=browser_execution_mode,
        api_base=resolve_browser_api_base(config),
        api_key=str(auth.openai_api_key or ""),
        model=str(config.agent.default_model or "gpt-5"),
        reasoning_effort=str(config.browser.reasoning_effort or "high"),
        headless=bool(config.browser.headless),
        keep_open=bool(config.browser.keep_open),
        timeout_ms=int(config.browser.timeout_ms or 45000),
        phase_timeout_seconds=int(config.browser.budgets.phase_timeout_seconds or 180),
        step_timeout_seconds=int(config.browser.budgets.step_timeout_seconds or 30),
        max_step_retries=int(config.browser.budgets.max_step_retries or 1),
        max_phase_steps=int(config.browser.budgets.max_phase_steps or 24),
        budgets=config.browser.budgets,
        guards=config.browser.guards,
        recovery=config.browser.recovery,
        retrieval_policy=config.browser.retrieval_policy,
        browser_name=str(config.browser.browser_name or "chrome"),
        executable_path=str(config.browser.executable_path or ""),
        phase_runtime_factory=_create_responses_browser_phase_runtime,
    )
    browser_runner.execution_backend = execution_backend
    loop = AgentLoop(
        project_root=project_root,
        workspace=resolved_workspace,
        provider=provider,
        model=config.agent.default_model,
        max_history_messages=config.agent.max_history_messages,
        related_history_k=config.agent.related_history_k,
        relatedness_threshold=config.agent.relatedness_threshold,
        router_confidence_threshold=config.agent.router_confidence_threshold,
        router_log_enabled=config.agent.router_log_enabled,
        search_company_top_k=config.agent.search_company_top_k,
        site_parallelism=config.agent.site_parallelism,
        site_tools=site_tools,
        browser_runner=browser_runner,
        browser_budgets=config.browser.budgets,
    )
    return loop, config
