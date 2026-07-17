"""Processor adapter interface for assistant bridge classification."""

from __future__ import annotations

from typing import Any, Protocol

from careereng.orchestration.agent_protocol.assistant_bridge import AssistantBridgeDecision


class ProcessorAdapter(Protocol):
    backend: str
    version: str

    def classify(self, *, message: str, context: dict[str, Any]) -> AssistantBridgeDecision:
        """Classify a bridge message and return a normalized decision."""
