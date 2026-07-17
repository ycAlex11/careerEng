"""Generic continuation dispatch for resumable workflow units.

The engine owns only the routing contract. Domain capabilities register the
handlers that know how to resume their own work; this module never chooses a
job, interprets a site state, or creates a business outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


ContinuationHandler = Callable[["ContinuationRequest"], Any]


@dataclass(frozen=True)
class ContinuationRequest:
    """A generic request to resume one previously paused workflow unit."""

    scope: str
    phase: str
    continuation: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def route_key(self) -> tuple[str, str]:
        return (str(self.scope or "").strip(), str(self.phase or "").strip())


class UnsupportedContinuationError(ValueError):
    """Raised when no domain capability registered the requested continuation."""


class ContinuationRegistry:
    """Register and route continuation handlers without owning their policy."""

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], ContinuationHandler] = {}

    def register(self, *, scope: str, phase: str, handler: ContinuationHandler) -> None:
        key = (str(scope or "").strip(), str(phase or "").strip())
        if not all(key):
            raise ValueError("continuation scope and phase are required")
        self._handlers[key] = handler

    def resume(self, request: ContinuationRequest) -> Any:
        handler = self._handlers.get(request.route_key)
        if handler is None:
            scope, phase = request.route_key
            raise UnsupportedContinuationError(
                f"unsupported continuation: scope={scope or '<missing>'} phase={phase or '<missing>'}"
            )
        return handler(request)

