"""Config dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentConfig:
    default_provider: str = "openrouter"
    default_model: str = "openai/gpt-4o-mini"
    max_history_messages: int = 50
    related_history_k: int = 6
    relatedness_threshold: float = 0.7
    site_parallelism: int = 2
    router_confidence_threshold: float = 0.75
    router_log_enabled: bool = True
    search_company_top_k: int = 10


@dataclass
class BrowserBudgetsConfig:
    phase_timeout_seconds: int = 180
    step_timeout_seconds: int = 30
    max_step_retries: int = 1
    max_phase_steps: int = 24
    session_preparation_phase_timeout_seconds: int = 420
    application_status_review_phase_timeout_seconds: int = 300
    job_filtering_phase_timeout_seconds: int = 420
    job_retrieval_phase_timeout_seconds: int = 1500
    job_retrieval_timeout_seconds_per_page: int = 180
    job_retrieval_timeout_max_pages: int = 10
    job_retrieval_step_timeout_seconds: int = 90
    job_retrieval_max_phase_steps: int = 96
    apply_phase_timeout_seconds: int = 3600
    apply_step_timeout_seconds: int = 300
    apply_max_phase_steps: int = 240
    apply_job_phase_timeout_seconds: int = 3600
    apply_job_timeout_ms: int = 180000
    apply_site_phase_budget_factor: float = 0.8
    debug_session_preparation_timeout_seconds: int = 600


@dataclass
class BrowserConfig:
    headless: bool = False
    keep_open: bool = False
    timeout_ms: int = 45000
    slow_mo_ms: int = 0
    reasoning_effort: str = "high"
    site_parallelism: int = 2
    browser_name: str = "chrome"
    mcp_port_start: int = 8931
    budgets: BrowserBudgetsConfig = field(default_factory=BrowserBudgetsConfig)


@dataclass
class PathsConfig:
    workspace: str = "./workspace"

    def workspace_path(self, project_root: Path) -> Path:
        raw = Path(self.workspace).expanduser()
        if raw.is_absolute():
            return raw
        return (project_root / raw).resolve()


@dataclass
class ProviderConfig:
    api_base: str = ""
    structured_output_mode: str = "auto"


@dataclass
class ProvidersConfig:
    openai: ProviderConfig = field(
        default_factory=lambda: ProviderConfig(api_base="https://api.openai.com/v1")
    )
    openrouter: ProviderConfig = field(
        default_factory=lambda: ProviderConfig(api_base="https://openrouter.ai/api/v1")
    )


@dataclass
class AppConfig:
    agent: AgentConfig
    browser: BrowserConfig
    paths: PathsConfig
    providers: ProvidersConfig


@dataclass
class AuthConfig:
    openai_api_key: str = ""
    openrouter_api_key: str = ""
