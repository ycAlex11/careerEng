"""External-agent bridge helpers for CareerEng."""

from .contracts import (
    AGENT_BRIDGE_MODE,
    AGENT_BRIDGE_PROTOCOL_VERSION,
    AGENT_BRIDGE_REQUIRED_REASON,
    AGENT_BRIDGE_STATUS,
    LEGACY_CODEX_HANDOFF_MODE,
    LEGACY_CODEX_HANDOFF_REASON,
    LEGACY_CODEX_HANDOFF_STATUS,
    is_agent_bridge_reason,
    normalize_execution_mode,
)

__all__ = [
    "AGENT_BRIDGE_MODE",
    "AGENT_BRIDGE_PROTOCOL_VERSION",
    "AGENT_BRIDGE_REQUIRED_REASON",
    "AGENT_BRIDGE_STATUS",
    "LEGACY_CODEX_HANDOFF_MODE",
    "LEGACY_CODEX_HANDOFF_REASON",
    "LEGACY_CODEX_HANDOFF_STATUS",
    "is_agent_bridge_reason",
    "normalize_execution_mode",
]
