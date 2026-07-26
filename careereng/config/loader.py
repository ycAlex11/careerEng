"""Load config.toml and auth.json."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from careereng.config.schema import (
    AgentConfig,
    AgentRecoveryConfig,
    AppConfig,
    AuthConfig,
    BrowserConfig,
    BrowserBudgetsConfig,
    BrowserGuardsConfig,
    BrowserRecoveryConfig,
    BrowserRetrievalPolicyConfig,
    SameUrlNoProgressGuardConfig,
    EvolutionApplyProbeConfig,
    EvolutionBatchReviewConfig,
    EvolutionConfig,
    EvolutionLoopConfig,
    ExecutionConfig,
    PathsConfig,
    ProviderConfig,
    ProvidersConfig,
    RetrievalConfig,
    RetrievalStopPolicyConfig,
)
from careereng.utils import write_json

try:  # Python 3.11+
    import tomllib  # type: ignore
except Exception:  # pragma: no cover
    tomllib = None


DEFAULT_CONFIG_TOML = """[workspace]
path = "./workspace"

[agent]
# LLM provider and model are intentionally not preset here.
# Choose values that match your own provider before running CareerEng.
# default_provider = "openai"
# default_model = "gpt-5"
max_history_messages = 50
related_history_k = 6
relatedness_threshold = 0.7
site_parallelism = 2
router_confidence_threshold = 0.75
router_log_enabled = true
search_company_top_k = 10

[agent.recovery]
# These are execution limits only. They do not decide browser actions.
idle_timeout_seconds = 180
max_resume_attempts = 2

[browser]
# provider: use the configured browser LLM API.
# agent_bridge: keep CareerEng's Playwright MCP runtime and let an external agent such as Codex drive it.
# codex_handoff is kept as a legacy alias for agent_bridge.
# codex_app_server creates a Codex worker thread per active site work item.
execution_mode = "provider"
headless = false
keep_open = false
timeout_ms = 45000
slow_mo_ms = 0
reasoning_effort = "high"
browser_name = "chrome"
# Optional browser executable for Playwright MCP, for example Chrome for Testing.
executable_path = ""
mcp_port_start = 8931

[execution]
# Both transports may be available, but every runtime host selects exactly one.
# CareerEng never changes this choice because a provider call fails or a Codex
# worker is unavailable. Existing config files without this section continue to
# derive the choice from browser.execution_mode.
provider_enabled = true
codex_enabled = true
selected_backend = "provider"

[browser.budgets]
phase_timeout_seconds = 180
step_timeout_seconds = 90
max_step_retries = 1
max_phase_steps = 24
session_preparation_phase_timeout_seconds = 420
application_status_review_phase_timeout_seconds = 600
job_filtering_phase_timeout_seconds = 420
job_retrieval_phase_timeout_seconds = 1500
job_retrieval_timeout_seconds_per_page = 180
job_retrieval_timeout_max_pages = 10
job_retrieval_step_timeout_seconds = 90
job_retrieval_max_phase_steps = 96
apply_phase_timeout_seconds = 3600
apply_step_timeout_seconds = 300
apply_max_phase_steps = 240
apply_job_phase_timeout_seconds = 3600
apply_job_timeout_ms = 180000
apply_site_phase_budget_factor = 0.8
inner_max_failures = 3
outer_max_attempts = 3
loop_control_refinement_attempts_per_batch = 3
loop_control_user_input_attempts_per_batch = 3
loop_control_outer_batch_attempts = 3
loop_control_failed_batches_per_pattern = 3
debug_session_preparation_timeout_seconds = 600

[browser.guards.same_url_no_progress]
tool_call_limit = 5
token_limit = 60000

[browser.guards.same_url_no_progress.phase_overrides.job_retrieval]
tool_call_limit = 8
token_limit = 160000

[browser.guards.same_url_no_progress.phase_overrides.apply]
tool_call_limit = 15
token_limit = 260000

[browser.recovery]
snapshot_timeout_seconds = 90
max_attempts = 3

[browser.recovery.tool_settle_policies.browser_file_upload]
max_snapshot_retries = 8
sleep_seconds = 2.0

[evolution.apply_probe]
max_attempted = 8
unsuccessful_threshold = 5

[evolution.batch_review]
site_run_threshold = 5

[evolution.loops]
# New-site exploration and its outer synthesis cadence. The agent decides
# whether an attempt succeeded; these are only structural boundaries.
inner_attempt_limit = 3
outer_batch_limit = 3

[retrieval.stop_policy]
history_stop_success_ratio = 0.4
history_stop_min_page_jobs = 10

# Optional LLM provider settings. Uncomment and edit these if you use a custom gateway.
# [providers.openai]
# api_base = "https://api.openai.com/v1"
# structured_output_mode = "auto"
"""


DEFAULT_AUTH = {
    "_note": "Enter your own provider API key here before running CareerEng.",
    "providers": {
        "openai": {"api_key": ""},
        "openrouter": {"api_key": ""},
    }
}


def _parse_toml_minimal(text: str) -> dict:
    data: dict = {}
    current: dict | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            if not section:
                current = None
                continue
            current = data
            for part in section.split("."):
                existing = current.get(part)
                if not isinstance(existing, dict):
                    existing = {}
                    current[part] = existing
                current = existing
            continue

        if "=" not in line or current is None:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if value.startswith('"') and value.endswith('"'):
            parsed = value[1:-1]
        elif value.lower() in {"true", "false"}:
            parsed = value.lower() == "true"
        else:
            try:
                parsed = int(value)
            except Exception:
                try:
                    parsed = float(value)
                except Exception:
                    parsed = value

        current[key] = parsed

    return data


def _coerce_int(value: object, default: int) -> int:
    try:
        return int(value if value is not None else default)
    except Exception:
        return int(default)


def _normalize_same_url_no_progress_guard(payload: dict) -> dict:
    if not isinstance(payload, dict):
        payload = {}
    default_policy = SameUrlNoProgressGuardConfig()
    nested = payload.get("same_url_no_progress")
    nested_payload = dict(nested) if isinstance(nested, dict) else {}

    legacy_tool_limit = payload.get("same_url_no_progress_tool_call_limit")
    legacy_token_limit = payload.get("same_url_no_progress_token_limit")
    nested_tool_limit = nested_payload.get("tool_call_limit")
    nested_token_limit = nested_payload.get("token_limit")
    if (
        legacy_tool_limit is not None
        and _coerce_int(legacy_tool_limit, default_policy.tool_call_limit) != default_policy.tool_call_limit
        and _coerce_int(nested_tool_limit, default_policy.tool_call_limit) == default_policy.tool_call_limit
    ):
        nested_tool_limit = legacy_tool_limit
    if (
        legacy_token_limit is not None
        and _coerce_int(legacy_token_limit, default_policy.token_limit) != default_policy.token_limit
        and _coerce_int(nested_token_limit, default_policy.token_limit) == default_policy.token_limit
    ):
        nested_token_limit = legacy_token_limit

    tool_call_limit = _coerce_int(nested_tool_limit, default_policy.tool_call_limit)
    token_limit = _coerce_int(nested_token_limit, default_policy.token_limit)
    raw_overrides = nested_payload.get("phase_overrides")
    phase_overrides: dict[str, dict[str, int]] = {}
    if isinstance(raw_overrides, dict):
        for phase, override in raw_overrides.items():
            if not isinstance(override, dict):
                continue
            phase_key = str(phase or "").strip()
            if not phase_key:
                continue
            phase_overrides[phase_key] = {
                "tool_call_limit": _coerce_int(override.get("tool_call_limit"), tool_call_limit),
                "token_limit": _coerce_int(override.get("token_limit"), token_limit),
            }

    legacy_apply_tool_limit = payload.get("apply_same_url_no_progress_tool_call_limit")
    legacy_apply_token_limit = payload.get("apply_same_url_no_progress_token_limit")
    default_apply_override = default_policy.phase_overrides["apply"]
    apply_override_is_default = phase_overrides.get("apply") == default_apply_override
    legacy_apply_differs = (
        legacy_apply_tool_limit is not None
        and _coerce_int(legacy_apply_tool_limit, default_apply_override["tool_call_limit"])
        != default_apply_override["tool_call_limit"]
    ) or (
        legacy_apply_token_limit is not None
        and _coerce_int(legacy_apply_token_limit, default_apply_override["token_limit"])
        != default_apply_override["token_limit"]
    )
    if ("apply" not in phase_overrides or apply_override_is_default) and legacy_apply_differs:
        phase_overrides["apply"] = {
            "tool_call_limit": _coerce_int(legacy_apply_tool_limit, tool_call_limit),
            "token_limit": _coerce_int(legacy_apply_token_limit, token_limit),
        }

    for phase, override in default_policy.phase_overrides.items():
        phase_overrides.setdefault(
            phase,
            {
                "tool_call_limit": _coerce_int(override.get("tool_call_limit"), tool_call_limit),
                "token_limit": _coerce_int(override.get("token_limit"), token_limit),
            },
        )

    payload["same_url_no_progress_tool_call_limit"] = tool_call_limit
    payload["same_url_no_progress_token_limit"] = token_limit
    apply_override = phase_overrides.get("apply", {})
    payload["apply_same_url_no_progress_tool_call_limit"] = _coerce_int(
        apply_override.get("tool_call_limit"),
        SameUrlNoProgressGuardConfig().phase_overrides["apply"]["tool_call_limit"],
    )
    payload["apply_same_url_no_progress_token_limit"] = _coerce_int(
        apply_override.get("token_limit"),
        SameUrlNoProgressGuardConfig().phase_overrides["apply"]["token_limit"],
    )
    payload["same_url_no_progress"] = SameUrlNoProgressGuardConfig(
        tool_call_limit=tool_call_limit,
        token_limit=token_limit,
        phase_overrides=phase_overrides,
    )
    return payload


def config_path(project_root: Path) -> Path:
    return project_root / "config.toml"


def auth_path(project_root: Path) -> Path:
    return project_root / "auth.json"


def ensure_files(project_root: Path) -> tuple[Path, Path]:
    cpath = config_path(project_root)
    apath = auth_path(project_root)
    if not cpath.exists():
        cpath.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    if not apath.exists():
        write_json(apath, DEFAULT_AUTH)
    return cpath, apath


def load_config(project_root: Path) -> AppConfig:
    cpath, _ = ensure_files(project_root)
    payload = {
        "agent": asdict(AgentConfig()),
        "browser": asdict(BrowserConfig()),
        "execution": asdict(ExecutionConfig()),
        "evolution": asdict(EvolutionConfig()),
        "retrieval": asdict(RetrievalConfig()),
        "paths": PathsConfig().__dict__.copy(),
        "providers": {
            "openai": ProviderConfig(api_base="https://api.openai.com/v1", structured_output_mode="auto").__dict__.copy(),
            "openrouter": ProviderConfig(api_base="https://openrouter.ai/api/v1", structured_output_mode="auto").__dict__.copy(),
        },
    }
    budget_keys = set(asdict(BrowserBudgetsConfig()).keys())
    guard_keys = set(asdict(BrowserGuardsConfig()).keys())
    recovery_keys = set(asdict(BrowserRecoveryConfig()).keys())
    retrieval_policy_keys = set(asdict(BrowserRetrievalPolicyConfig()).keys())
    apply_probe_keys = set(asdict(EvolutionApplyProbeConfig()).keys())
    batch_review_keys = set(asdict(EvolutionBatchReviewConfig()).keys())
    loop_keys = set(asdict(EvolutionLoopConfig()).keys())
    stop_policy_keys = set(asdict(RetrievalStopPolicyConfig()).keys())
    loaded_apply_probe_config = False
    loaded_evolution_loop_config = False
    loaded_retrieval_stop_policy_config = False
    loaded_execution_config = False
    try:
        text = cpath.read_text(encoding="utf-8")
        if tomllib is not None:
            loaded = tomllib.loads(text)
        else:
            loaded = _parse_toml_minimal(text)
    except Exception:
        loaded = {}

    if isinstance(loaded, dict):
        agent = loaded.get("agent")
        if isinstance(agent, dict):
            for key, value in agent.items():
                if key == "recovery" and isinstance(value, dict):
                    for recovery_key, recovery_value in value.items():
                        if recovery_key in payload["agent"]["recovery"]:
                            payload["agent"]["recovery"][recovery_key] = recovery_value
                    continue
                if key in payload["agent"]:
                    payload["agent"][key] = value

        browser = loaded.get("browser")
        if isinstance(browser, dict):
            for key, value in browser.items():
                if key == "budgets" and isinstance(value, dict):
                    for budget_key, budget_value in value.items():
                        if budget_key in budget_keys:
                            payload["browser"]["budgets"][budget_key] = budget_value
                        elif budget_key in guard_keys:
                            # Compatibility for guard values temporarily placed under [browser.budgets].
                            payload["browser"]["guards"][budget_key] = budget_value
                    continue
                if key == "guards" and isinstance(value, dict):
                    for guard_key, guard_value in value.items():
                        if guard_key in guard_keys:
                            payload["browser"]["guards"][guard_key] = guard_value
                    continue
                if key == "recovery" and isinstance(value, dict):
                    for recovery_key, recovery_value in value.items():
                        if recovery_key in recovery_keys:
                            payload["browser"]["recovery"][recovery_key] = recovery_value
                    continue
                if key == "retrieval_policy" and isinstance(value, dict):
                    for policy_key, policy_value in value.items():
                        if policy_key in retrieval_policy_keys:
                            payload["browser"]["retrieval_policy"][policy_key] = policy_value
                            if policy_key in stop_policy_keys:
                                payload["retrieval"]["stop_policy"][policy_key] = policy_value
                    continue
                if key in budget_keys:
                    # Backward compatibility for older root-level [browser] budget keys.
                    payload["browser"]["budgets"][key] = value
                    continue
                if key in guard_keys:
                    # Backward compatibility for older root-level [browser] guard keys.
                    payload["browser"]["guards"][key] = value
                    continue
                if key in recovery_keys:
                    # Backward compatibility for root-level [browser] recovery keys.
                    payload["browser"]["recovery"][key] = value
                    continue
                if key in retrieval_policy_keys:
                    # Backward compatibility for root-level [browser] retrieval policy keys.
                    payload["browser"]["retrieval_policy"][key] = value
                    if key in stop_policy_keys:
                        payload["retrieval"]["stop_policy"][key] = value
                    continue
                if key in payload["browser"] and key not in {"budgets", "guards", "retrieval_policy"}:
                    payload["browser"][key] = value

        execution = loaded.get("execution")
        if isinstance(execution, dict):
            loaded_execution_config = True
            for key, value in execution.items():
                if key in payload["execution"]:
                    payload["execution"][key] = value

    if not loaded_execution_config:
        # Existing files used browser.execution_mode as the sole explicit
        # backend selection. Keep that choice until users add [execution].
        payload["execution"]["selected_backend"] = ""

        evolution = loaded.get("evolution")
        if isinstance(evolution, dict):
            apply_probe = evolution.get("apply_probe")
            if isinstance(apply_probe, dict):
                loaded_apply_probe_config = True
                for key, value in apply_probe.items():
                    if key in apply_probe_keys:
                        payload["evolution"]["apply_probe"][key] = value
            batch_review = evolution.get("batch_review")
            if isinstance(batch_review, dict):
                for key, value in batch_review.items():
                    if key in batch_review_keys:
                        payload["evolution"]["batch_review"][key] = value
            loops = evolution.get("loops")
            if isinstance(loops, dict):
                loaded_evolution_loop_config = True
                for key, value in loops.items():
                    if key in loop_keys:
                        payload["evolution"]["loops"][key] = value

        retrieval = loaded.get("retrieval")
        if isinstance(retrieval, dict):
            stop_policy = retrieval.get("stop_policy")
            if isinstance(stop_policy, dict):
                loaded_retrieval_stop_policy_config = True
                for key, value in stop_policy.items():
                    if key in stop_policy_keys:
                        payload["retrieval"]["stop_policy"][key] = value

        workspace = loaded.get("workspace")
        if isinstance(workspace, dict) and isinstance(workspace.get("path"), str):
            payload["paths"]["workspace"] = workspace["path"]

        # Legacy format compatibility.
        paths = loaded.get("paths")
        if isinstance(paths, dict) and isinstance(paths.get("workspace"), str):
            payload["paths"]["workspace"] = paths["workspace"]

        providers = loaded.get("providers")
        if isinstance(providers, dict):
            for name in ("openai", "openrouter"):
                sec = providers.get(name)
                if isinstance(sec, dict):
                    for key, value in sec.items():
                        if key in payload["providers"][name]:
                            if key == "api_base" and not str(value or "").strip():
                                continue
                            payload["providers"][name][key] = value

    browser_payload = dict(payload["browser"])
    browser_budgets_payload = browser_payload.pop("budgets", {})
    browser_guards_payload = browser_payload.pop("guards", {})
    browser_guards_payload = _normalize_same_url_no_progress_guard(browser_guards_payload)
    browser_recovery_payload = browser_payload.pop("recovery", {})
    browser_retrieval_policy_payload = browser_payload.pop("retrieval_policy", {})
    evolution_payload = dict(payload["evolution"])
    evolution_apply_probe_payload = dict(evolution_payload.get("apply_probe") or {})
    evolution_batch_review_payload = dict(evolution_payload.get("batch_review") or {})
    evolution_loops_payload = dict(evolution_payload.get("loops") or {})
    retrieval_payload = dict(payload["retrieval"])
    retrieval_stop_policy_payload = dict(retrieval_payload.get("stop_policy") or {})

    # Keep older runtime wiring working while exposing clearer top-level config sections.
    if not loaded_apply_probe_config:
        evolution_apply_probe_payload["max_attempted"] = browser_budgets_payload.get(
            "apply_probe_max_attempted",
            EvolutionApplyProbeConfig().max_attempted,
        )
        evolution_apply_probe_payload["unsuccessful_threshold"] = browser_budgets_payload.get(
            "apply_probe_unsuccessful_threshold",
            EvolutionApplyProbeConfig().unsuccessful_threshold,
        )
    browser_budgets_payload["apply_probe_max_attempted"] = int(
        evolution_apply_probe_payload.get(
            "max_attempted",
            browser_budgets_payload.get("apply_probe_max_attempted", BrowserBudgetsConfig().apply_probe_max_attempted),
        )
        or 0
    )
    browser_budgets_payload["apply_probe_unsuccessful_threshold"] = int(
        evolution_apply_probe_payload.get(
            "unsuccessful_threshold",
            browser_budgets_payload.get(
                "apply_probe_unsuccessful_threshold",
                BrowserBudgetsConfig().apply_probe_unsuccessful_threshold,
            ),
        )
        or 0
    )
    # Existing loop engines consume BrowserBudgetsConfig. Keep that execution
    # contract while making the reusable evolution-loop cadence configurable
    # from the dedicated evolution section.
    if loaded_evolution_loop_config:
        browser_budgets_payload["inner_max_failures"] = int(
            evolution_loops_payload.get("inner_attempt_limit", EvolutionLoopConfig().inner_attempt_limit) or 1
        )
        browser_budgets_payload["outer_max_attempts"] = int(
            evolution_loops_payload.get("outer_batch_limit", EvolutionLoopConfig().outer_batch_limit) or 1
        )
    else:
        evolution_loops_payload["inner_attempt_limit"] = int(
            browser_budgets_payload.get("inner_max_failures", EvolutionLoopConfig().inner_attempt_limit) or 1
        )
        evolution_loops_payload["outer_batch_limit"] = int(
            browser_budgets_payload.get("outer_max_attempts", EvolutionLoopConfig().outer_batch_limit) or 1
        )
    if not loaded_retrieval_stop_policy_config:
        for key in stop_policy_keys:
            retrieval_stop_policy_payload[key] = browser_retrieval_policy_payload.get(
                key,
                getattr(RetrievalStopPolicyConfig(), key),
            )
    for key in stop_policy_keys:
        value = retrieval_stop_policy_payload.get(key, browser_retrieval_policy_payload.get(key))
        if value is not None:
            browser_retrieval_policy_payload[key] = value

    return AppConfig(
        agent=AgentConfig(
            **{
                **payload["agent"],
                "recovery": AgentRecoveryConfig(**dict(payload["agent"].get("recovery") or {})),
            }
        ),
        browser=BrowserConfig(
            **browser_payload,
            budgets=BrowserBudgetsConfig(**browser_budgets_payload),
            guards=BrowserGuardsConfig(**browser_guards_payload),
            recovery=BrowserRecoveryConfig(**browser_recovery_payload),
            retrieval_policy=BrowserRetrievalPolicyConfig(**browser_retrieval_policy_payload),
        ),
        execution=ExecutionConfig(**payload["execution"]),
        evolution=EvolutionConfig(
            apply_probe=EvolutionApplyProbeConfig(**evolution_apply_probe_payload),
            batch_review=EvolutionBatchReviewConfig(**evolution_batch_review_payload),
            loops=EvolutionLoopConfig(**evolution_loops_payload),
        ),
        retrieval=RetrievalConfig(stop_policy=RetrievalStopPolicyConfig(**retrieval_stop_policy_payload)),
        paths=PathsConfig(**payload["paths"]),
        providers=ProvidersConfig(
            openai=ProviderConfig(**payload["providers"]["openai"]),
            openrouter=ProviderConfig(**payload["providers"]["openrouter"]),
        ),
    )


def load_auth(project_root: Path) -> AuthConfig:
    _, apath = ensure_files(project_root)
    try:
        data = json.loads(apath.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    openai_key = ""
    openrouter_key = ""

    # Preferred format.
    providers = data.get("providers")
    if isinstance(providers, dict):
        openai = providers.get("openai")
        if isinstance(openai, dict) and isinstance(openai.get("api_key"), str):
            openai_key = openai["api_key"]

        openrouter = providers.get("openrouter")
        if isinstance(openrouter, dict) and isinstance(openrouter.get("api_key"), str):
            openrouter_key = openrouter["api_key"]

    # Legacy compatibility.
    if not openai_key and isinstance(data.get("openai_api_key"), str):
        openai_key = data["openai_api_key"]
    if not openrouter_key and isinstance(data.get("openrouter_api_key"), str):
        openrouter_key = data["openrouter_api_key"]

    return AuthConfig(
        openai_api_key=openai_key,
        openrouter_api_key=openrouter_key,
    )
