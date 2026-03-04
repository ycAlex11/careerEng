"""Provider abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ProviderError(RuntimeError):
    """Raised when provider request fails."""


class LLMProvider(ABC):
    @abstractmethod
    def chat(self, messages: list[dict[str, Any]], *, model: str) -> str:
        """Run one chat completion call and return text."""
