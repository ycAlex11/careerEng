"""Execution-backend policy shared by host, CLI, and batch persistence."""

from __future__ import annotations

from typing import Any


PROVIDER_BACKEND = "provider"
CODEX_BACKEND = "codex"
EXECUTION_BACKENDS = frozenset({PROVIDER_BACKEND, CODEX_BACKEND})


def normalize_execution_backend(value: object) -> str:
    """Normalize public config/request aliases without choosing a fallback."""

    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in {"provider", "api", "openai", "openrouter"}:
        return PROVIDER_BACKEND
    if normalized in {
        "codex",
        "codex_app_server",
        "codex_appserver",
        "codex_workers",
        "agent_bridge",
        "codex_handoff",
    }:
        return CODEX_BACKEND
    return ""


def execution_backend_from_mode(execution_mode: object) -> str:
    """Map the legacy browser mode to its owning execution backend."""

    return normalize_execution_backend(execution_mode) or PROVIDER_BACKEND


def resolve_execution_backend(
    config: Any,
    *,
    requested_backend: object = "",
    runtime_execution_mode: object = "",
) -> tuple[str, str]:
    """Return one configured backend or a descriptive policy error.

    The configuration selects the active backend. A request may repeat that
    choice, but it cannot select a different transport inside an existing host.
    """

    requested_raw = str(requested_backend or "").strip()
    requested = normalize_execution_backend(requested_raw)
    if requested_raw and not requested:
        return "", f"unsupported execution backend: {requested_raw}"

    execution = getattr(config, "execution", None)
    selected_raw = str(getattr(execution, "selected_backend", "") or "").strip()
    selected = normalize_execution_backend(selected_raw)
    if selected_raw and not selected:
        return "", f"unsupported configured execution backend: {selected_raw}"
    if not selected:
        browser = getattr(config, "browser", None)
        selected = execution_backend_from_mode(
            getattr(browser, "execution_mode", "") or runtime_execution_mode
        )

    if requested and requested != selected:
        return "", (
            f"execution backend mismatch: requested={requested} configured={selected}; "
            "CareerEng does not switch backends at runtime"
        )

    provider_enabled = bool(getattr(execution, "provider_enabled", True))
    codex_enabled = bool(getattr(execution, "codex_enabled", True))
    if selected == PROVIDER_BACKEND and not provider_enabled:
        return "", "provider execution backend is disabled by config"
    if selected == CODEX_BACKEND and not codex_enabled:
        return "", "codex execution backend is disabled by config"
    return selected, ""
