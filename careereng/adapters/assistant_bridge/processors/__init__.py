"""Assistant bridge processor adapters."""

from .base import ProcessorAdapter
from .local import LocalProcessorAdapter

__all__ = ["LocalProcessorAdapter", "ProcessorAdapter"]
