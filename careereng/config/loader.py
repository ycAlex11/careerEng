"""Load config.toml and auth.json."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from careereng.config.schema import (
    AgentConfig,
    AppConfig,
    AuthConfig,
    BrowserConfig,
    BrowserBudgetsConfig,
    PathsConfig,
    ProviderConfig,
    ProvidersConfig,
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

[browser]
headless = false
keep_open = false
timeout_ms = 45000
slow_mo_ms = 0
reasoning_effort = "high"
site_parallelism = 2
browser_name = "chrome"
mcp_port_start = 8931

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
debug_session_preparation_timeout_seconds = 600

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
        "agent": AgentConfig().__dict__.copy(),
        "browser": asdict(BrowserConfig()),
        "paths": PathsConfig().__dict__.copy(),
        "providers": {
            "openai": ProviderConfig(api_base="https://api.openai.com/v1", structured_output_mode="auto").__dict__.copy(),
            "openrouter": ProviderConfig(api_base="https://openrouter.ai/api/v1", structured_output_mode="auto").__dict__.copy(),
        },
    }
    budget_keys = set(asdict(BrowserBudgetsConfig()).keys())
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
                if key in payload["agent"]:
                    payload["agent"][key] = value

        browser = loaded.get("browser")
        if isinstance(browser, dict):
            for key, value in browser.items():
                if key == "budgets" and isinstance(value, dict):
                    for budget_key, budget_value in value.items():
                        if budget_key in budget_keys:
                            payload["browser"]["budgets"][budget_key] = budget_value
                    continue
                if key in budget_keys:
                    # Backward compatibility for older root-level [browser] budget keys.
                    payload["browser"]["budgets"][key] = value
                    continue
                if key in payload["browser"] and key != "budgets":
                    payload["browser"][key] = value

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

    return AppConfig(
        agent=AgentConfig(**payload["agent"]),
        browser=BrowserConfig(
            **browser_payload,
            budgets=BrowserBudgetsConfig(**browser_budgets_payload),
        ),
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
