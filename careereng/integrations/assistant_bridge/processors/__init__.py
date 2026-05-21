"""Assistant bridge processor adapters."""

from careereng.integrations.assistant_bridge.processors.base import ProcessorAdapter
from careereng.integrations.assistant_bridge.processors.local import LocalProcessorAdapter

__all__ = ["LocalProcessorAdapter", "ProcessorAdapter"]

