"""Driver-neutral phase-sequence progression.

The engine only understands a declared sequence and a structured phase result.
It does not decide application policy, matching, site behavior, or which career
domain work item should be created after a sequence completes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PhaseTransition:
    action: str
    current_phase: str
    next_phase: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "action": self.action,
            "current_phase": self.current_phase,
            "next_phase": self.next_phase,
            "reason": self.reason,
        }


def advance_phase_sequence(
    phase_slugs: tuple[str, ...] | list[str],
    *,
    current_phase: str,
    result_status: str,
) -> PhaseTransition:
    """Translate a recorded phase result into a generic sequence transition."""

    sequence = tuple(str(item or "").strip() for item in phase_slugs if str(item or "").strip())
    current = str(current_phase or "").strip()
    status = str(result_status or "").strip().lower()

    if not sequence or not current or current not in sequence:
        return PhaseTransition(
            action="invalid_sequence",
            current_phase=current,
            reason="current phase is not present in the declared sequence",
        )
    if status == "waiting_user":
        return PhaseTransition(
            action="continue_current",
            current_phase=current,
            reason="the current phase is waiting for a user-supplied fact",
        )
    if status == "blocked":
        # Backwards compatibility for old provider work orders. New agent
        # work should use waiting_user for resumable user interaction.
        return PhaseTransition(
            action="continue_current",
            current_phase=current,
            reason="legacy blocked phase result is waiting for external or user input",
        )
    if status != "done":
        return PhaseTransition(
            action="invalid_result",
            current_phase=current,
            reason=f"unsupported phase result status: {status or '<missing>'}",
        )

    current_index = sequence.index(current)
    if current_index + 1 >= len(sequence):
        return PhaseTransition(
            action="complete_sequence",
            current_phase=current,
            reason="the current phase completed the declared sequence",
        )
    return PhaseTransition(
        action="advance_phase",
        current_phase=current,
        next_phase=sequence[current_index + 1],
        reason="the current phase completed successfully",
    )
