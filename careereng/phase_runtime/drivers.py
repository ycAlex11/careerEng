"""Driver contracts for phase-runtime callers."""

from __future__ import annotations

from typing import Any, Protocol


class PhaseDriver(Protocol):
    """A provider, Codex, or external agent that can consume a phase session."""

    def run_phase(self, session: dict[str, Any]) -> dict[str, Any]:
        """Run one phase session and return a structured phase result."""
