"""Agent-visible CareerEng contracts shared by every adapter."""

from careereng.orchestration.agent_protocol.browser_phase import BrowserPhaseResult
from careereng.orchestration.agent_protocol.drivers import PhaseDriver
from careereng.orchestration.agent_protocol.llm import LLMProvider, ProviderError, StructuredOutputResult
from careereng.orchestration.agent_protocol.results import phase_result_payload
from careereng.orchestration.agent_protocol.browser_sequence import BROWSER_SEQUENCE_TOOL, browser_sequence_tool_schema
from careereng.orchestration.agent_protocol.codex import codex_event, codex_operation_failed, codex_operation_succeeded
from careereng.orchestration.agent_protocol.state_tools import (
    DEFAULT_STATE_TOOL_REGISTRY,
    StateToolRegistry,
    StateToolSpec,
    state_tool_schema,
    state_tool_schemas_for_phase,
)

__all__ = [
    "DEFAULT_STATE_TOOL_REGISTRY",
    "BrowserPhaseResult",
    "LLMProvider",
    "PhaseDriver",
    "ProviderError",
    "StateToolRegistry",
    "StateToolSpec",
    "state_tool_schema",
    "state_tool_schemas_for_phase",
    "StructuredOutputResult",
    "phase_result_payload",
    "BROWSER_SEQUENCE_TOOL",
    "browser_sequence_tool_schema",
    "codex_event",
    "codex_operation_failed",
    "codex_operation_succeeded",
]
