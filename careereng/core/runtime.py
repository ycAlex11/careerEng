"""Shared runtime builders for CLI and workspace manager."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.agent.channel_locator import ChannelLocator
from careereng.agent.loop import AgentLoop
from careereng.browser_controls import BrowserAutomationService
from careereng.config.loader import load_auth, load_config
from careereng.providers import create_provider
from careereng.providers.base import ProviderError, StructuredOutputResult
from careereng.storage.search_store import SearchStore
from careereng.storage.site_store import SiteStore
from careereng.tools.site_tools import SiteTools


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


def project_root_from_cwd(cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    if (base / "pyproject.toml").exists() and (base / "careereng").exists():
        return base
    return Path(__file__).resolve().parents[1]


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
    try:
        _, provider = create_provider(config, auth)
    except ProviderError as exc:
        provider = FallbackProvider(str(exc))

    resolved_workspace = workspace or config.paths.workspace_path(project_root)
    resolved_workspace.mkdir(parents=True, exist_ok=True)
    site_store = SiteStore(resolved_workspace, project_root=project_root)
    site_tools = SiteTools(site_store)
    site_tools.project_root = project_root
    browser_runner = BrowserAutomationService(
        project_root=project_root,
        workspace=resolved_workspace,
        site_store=site_store,
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
        browser_name=str(config.browser.browser_name or "chrome"),
    )
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
