"""Config dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentRecoveryConfig:
    """Mechanical limits for external-agent no-progress recovery."""

    idle_timeout_seconds: int = 180
    max_resume_attempts: int = 2
    interrupt_ack_timeout_seconds: int = 15
    max_interrupt_attempts: int = 2


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
    recovery: AgentRecoveryConfig = field(default_factory=AgentRecoveryConfig)


@dataclass
class BrowserBudgetsConfig:
    phase_timeout_seconds: int = 180
    step_timeout_seconds: int = 90
    max_step_retries: int = 1
    max_phase_steps: int = 24
    session_preparation_phase_timeout_seconds: int = 420
    application_status_review_phase_timeout_seconds: int = 600
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
    apply_probe_max_attempted: int = 8
    apply_probe_unsuccessful_threshold: int = 5
    inner_max_failures: int = 3
    outer_max_attempts: int = 3
    loop_control_refinement_attempts_per_batch: int = 3
    loop_control_user_input_attempts_per_batch: int = 3
    loop_control_outer_batch_attempts: int = 3
    loop_control_failed_batches_per_pattern: int = 3
    debug_session_preparation_timeout_seconds: int = 600


@dataclass
class SameUrlNoProgressGuardConfig:
    tool_call_limit: int = 5
    token_limit: int = 60000
    phase_overrides: dict[str, dict[str, int]] = field(
        default_factory=lambda: {
            "job_retrieval": {
                "tool_call_limit": 8,
                "token_limit": 160000,
            },
            "apply": {
                "tool_call_limit": 15,
                "token_limit": 260000,
            },
        }
    )


@dataclass
class BrowserGuardsConfig:
    same_url_no_progress_tool_call_limit: int = 5
    same_url_no_progress_token_limit: int = 60000
    apply_same_url_no_progress_tool_call_limit: int = 15
    apply_same_url_no_progress_token_limit: int = 260000
    same_url_no_progress: SameUrlNoProgressGuardConfig = field(default_factory=SameUrlNoProgressGuardConfig)


@dataclass
class BrowserRecoveryConfig:
    snapshot_timeout_seconds: int = 90
    max_attempts: int = 3
    tool_settle_policies: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            "browser_file_upload": {
                "max_snapshot_retries": 8,
                "sleep_seconds": 2.0,
            }
        }
    )


@dataclass
class BrowserRetrievalPolicyConfig:
    history_stop_success_ratio: float = 0.4
    history_stop_min_page_jobs: int = 10


@dataclass
class EvolutionApplyProbeConfig:
    max_attempted: int = 8
    unsuccessful_threshold: int = 5


@dataclass
class EvolutionBatchReviewConfig:
    """Structural cadence for asking the user to review site evolution."""

    site_run_threshold: int = 5


@dataclass
class EvolutionLoopConfig:
    """Configuration-only limits for reusable evolution loop scopes.

    Python records these boundaries but leaves success, continuation, and
    proposed changes to the active external agent and its Skills.
    """

    inner_attempt_limit: int = 3
    outer_batch_limit: int = 3


@dataclass
class EvolutionConfig:
    apply_probe: EvolutionApplyProbeConfig = field(default_factory=EvolutionApplyProbeConfig)
    batch_review: EvolutionBatchReviewConfig = field(default_factory=EvolutionBatchReviewConfig)
    loops: EvolutionLoopConfig = field(default_factory=EvolutionLoopConfig)


@dataclass
class RetrievalStopPolicyConfig:
    history_stop_success_ratio: float = 0.4
    history_stop_min_page_jobs: int = 10


@dataclass
class RetrievalConfig:
    stop_policy: RetrievalStopPolicyConfig = field(default_factory=RetrievalStopPolicyConfig)


@dataclass
class BrowserConfig:
    execution_mode: str = "provider"
    headless: bool = False
    keep_open: bool = False
    timeout_ms: int = 45000
    slow_mo_ms: int = 0
    reasoning_effort: str = "high"
    browser_name: str = "chrome"
    executable_path: str = ""
    mcp_port_start: int = 8931
    budgets: BrowserBudgetsConfig = field(default_factory=BrowserBudgetsConfig)
    guards: BrowserGuardsConfig = field(default_factory=BrowserGuardsConfig)
    recovery: BrowserRecoveryConfig = field(default_factory=BrowserRecoveryConfig)
    retrieval_policy: BrowserRetrievalPolicyConfig = field(default_factory=BrowserRetrievalPolicyConfig)


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
class ExecutionConfig:
    """Availability and explicit selection of browser-execution transports."""

    provider_enabled: bool = True
    codex_enabled: bool = True
    selected_backend: str = "provider"


@dataclass
class AppConfig:
    agent: AgentConfig
    browser: BrowserConfig
    execution: ExecutionConfig
    evolution: EvolutionConfig
    retrieval: RetrievalConfig
    paths: PathsConfig
    providers: ProvidersConfig


@dataclass
class AuthConfig:
    openai_api_key: str = ""
    openrouter_api_key: str = ""
