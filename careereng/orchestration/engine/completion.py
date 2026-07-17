"""Generic phase-sequence completion signals for workflow orchestrators."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PhaseSequenceCompletion:
    """Data emitted when an external agent completes a declared phase sequence.

    The signal is intentionally domain-neutral. A workflow orchestrator decides
    whether a completed sequence starts another domain capability.
    """

    site_key: str
    batch_id: str
    session_id: str
    turn_id: str
    terminal_phase: str

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": "phase_sequence_completion",
            "site_key": self.site_key,
            "batch_id": self.batch_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "terminal_phase": self.terminal_phase,
        }
