"""Shared contracts for external-agent bridge modes."""

from __future__ import annotations


PROVIDER_MODE = "provider"
AGENT_BRIDGE_MODE = "agent_bridge"
LEGACY_CODEX_HANDOFF_MODE = "codex_handoff"

AGENT_BRIDGE_STATUS = "agent_bridge"
LEGACY_CODEX_HANDOFF_STATUS = "codex_handoff"

AGENT_BRIDGE_REQUIRED_REASON = "agent_bridge_required"
LEGACY_CODEX_HANDOFF_REASON = "codex_handoff_required"

AGENT_BRIDGE_ALIASES = {
    AGENT_BRIDGE_MODE,
    "agent",
    "agent-handoff",
    "agent_handoff",
    "external-agent",
    "external_agent",
    "external-agent-bridge",
    "external_agent_bridge",
    "codex",
    "codex-bridge",
    "codex_bridge",
    LEGACY_CODEX_HANDOFF_MODE,
    "handoff",
}


def normalize_execution_mode(value: str) -> str:
    mode = str(value or PROVIDER_MODE).strip().lower().replace("-", "_")
    if mode in {alias.replace("-", "_") for alias in AGENT_BRIDGE_ALIASES}:
        return AGENT_BRIDGE_MODE
    return PROVIDER_MODE


def is_agent_bridge_reason(value: str) -> bool:
    return str(value or "").strip() in {
        AGENT_BRIDGE_REQUIRED_REASON,
        LEGACY_CODEX_HANDOFF_REASON,
    }


def agent_bridge_phase(value: str = "") -> str:
    return str(value or "").strip() or AGENT_BRIDGE_STATUS
