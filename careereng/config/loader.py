"""Load config.toml and auth.json."""

from __future__ import annotations

import json
from pathlib import Path

from careereng.config.schema import (
    AgentConfig,
    AppConfig,
    AuthConfig,
    BrowserConfig,
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
default_provider = "openrouter"
default_model = "openai/gpt-4o-mini"
max_history_messages = 50
related_history_k = 6
relatedness_threshold = 0.7

[browser]
headless = true
timeout_ms = 45000

[providers.openrouter]
api_base = "https://openrouter.ai/api/v1"

[providers.openai]
api_base = "https://api.openai.com/v1"
"""


DEFAULT_AUTH = {
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
        "browser": BrowserConfig().__dict__.copy(),
        "paths": PathsConfig().__dict__.copy(),
        "providers": {
            "openai": ProviderConfig(api_base="https://api.openai.com/v1").__dict__.copy(),
            "openrouter": ProviderConfig(api_base="https://openrouter.ai/api/v1").__dict__.copy(),
        },
    }
    try:
        text = cpath.read_text(encoding="utf-8")
        if tomllib is not None:
            loaded = tomllib.loads(text)
        else:
            loaded = _parse_toml_minimal(text)
    except Exception:
        loaded = {}

    if isinstance(loaded, dict):
        for section in ("agent", "browser"):
            sec = loaded.get(section)
            if isinstance(sec, dict):
                payload[section].update(sec)

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
                    payload["providers"][name].update(sec)

    return AppConfig(
        agent=AgentConfig(**payload["agent"]),
        browser=BrowserConfig(**payload["browser"]),
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
