"""Backend-neutral orchestration state for declared browser phases.

This module consumes structured tool evidence only. It does not inspect a site,
page DOM, job description, or Skill. Backends use the same state transition so
that a provider response loop and an external agent worker cannot diverge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


RETRIEVAL_HISTORY_STOP_PENDING_KEY = "retrieval_history_stop_confirmation"
RETRIEVAL_HISTORY_STOP_CONFIRMED_KEY = "retrieval_history_stop_confirmed"
RETRIEVAL_HISTORY_STOP_STREAK_KEY = "retrieval_history_stop_streak"


@dataclass(frozen=True)
class RetrievalHistoryTransition:
    """One generic transition produced after recording a retrieval page."""

    action: str
    message: str = ""
    streak: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"action": self.action, "message": self.message, "streak": self.streak}


@dataclass(frozen=True)
class PhaseCompletionGate:
    """Whether a declared phase may be completed from current shared state."""

    allowed: bool
    message: str = ""


@dataclass(frozen=True)
class RetrievalPaginationGate:
    """Whether a pagination request is compatible with shared retrieval state."""

    allowed: bool
    message: str = ""


def record_retrieval_history_evidence(phase_memory: Any, structured: dict[str, Any]) -> RetrievalHistoryTransition:
    """Persist generic history-stop confirmation state after ``record_jobs``.

    ``stop_recommended`` remains evidence from storage, not a terminal command.
    The first matching page requires one further recorded page before completion
    can be accepted. A non-matching confirmation page clears the pending stop.
    """

    if phase_memory is None or not isinstance(structured, dict):
        return RetrievalHistoryTransition(action="untracked")

    stop_recommended = bool(structured.get("stop_recommended"))
    pending = str(getattr(phase_memory, "pending", {}).get(RETRIEVAL_HISTORY_STOP_PENDING_KEY) or "").strip()
    current_streak = _phase_metric(phase_memory, RETRIEVAL_HISTORY_STOP_STREAK_KEY)

    if not stop_recommended:
        if pending:
            phase_memory.drop(RETRIEVAL_HISTORY_STOP_PENDING_KEY, RETRIEVAL_HISTORY_STOP_STREAK_KEY)
            phase_memory.set_confirmed(
                key=RETRIEVAL_HISTORY_STOP_CONFIRMED_KEY,
                text="The history-stop confirmation page did not repeat the threshold signal; continue under the active Skill.",
            )
            return RetrievalHistoryTransition(
                action="confirmation_not_repeated",
                message="The confirmation page did not repeat the history-stop signal. Continue under the active Skill.",
            )
        phase_memory.drop(RETRIEVAL_HISTORY_STOP_STREAK_KEY)
        return RetrievalHistoryTransition(action="continue_by_skill")

    streak = current_streak + 1
    phase_memory.set_metric(key=RETRIEVAL_HISTORY_STOP_STREAK_KEY, value=streak)
    if streak == 1:
        message = (
            "The current recorded retrieval page reached the history-stop threshold. "
            "This is advisory evidence only: record one real next results page before completing retrieval."
        )
        phase_memory.drop(RETRIEVAL_HISTORY_STOP_CONFIRMED_KEY)
        phase_memory.set_pending(key=RETRIEVAL_HISTORY_STOP_PENDING_KEY, text=message)
        return RetrievalHistoryTransition(action="confirmation_required", message=message, streak=streak)

    phase_memory.drop(RETRIEVAL_HISTORY_STOP_PENDING_KEY)
    message = "A second recorded retrieval page also reached the history-stop threshold; completion is now allowed by the generic history policy."
    phase_memory.set_confirmed(key=RETRIEVAL_HISTORY_STOP_CONFIRMED_KEY, text=message)
    return RetrievalHistoryTransition(action="confirmation_recorded", message=message, streak=streak)


def phase_completion_gate(*, phase_slug: str, result_status: str, phase_memory: Any) -> PhaseCompletionGate:
    """Reject only a premature terminal result required by shared progression."""

    if str(phase_slug or "").strip() != "job_retrieval":
        return PhaseCompletionGate(allowed=True)
    if str(result_status or "").strip().lower() != "done":
        return PhaseCompletionGate(allowed=True)
    pending = str(getattr(phase_memory, "pending", {}).get(RETRIEVAL_HISTORY_STOP_PENDING_KEY) or "").strip()
    if not pending:
        return PhaseCompletionGate(allowed=True)
    return PhaseCompletionGate(
        allowed=False,
        message=(
            "Retrieval cannot complete yet: the first history-stop page still requires one real next results page "
            "to be recorded. Follow the active site Skill for pagination."
        ),
    )


def retrieval_pagination_gate(*, phase_slug: str, phase_memory: Any) -> RetrievalPaginationGate:
    """Apply the generic confirmation-page contract before another pagination action."""

    if str(phase_slug or "").strip() != "job_retrieval":
        return RetrievalPaginationGate(allowed=True)
    pending = str(getattr(phase_memory, "pending", {}).get(RETRIEVAL_HISTORY_STOP_PENDING_KEY) or "").strip()
    if pending:
        return RetrievalPaginationGate(
            allowed=True,
            message="The history-stop threshold needs one confirmation page. One next-results action is allowed; record that page before completing retrieval.",
        )
    confirmed = str(getattr(phase_memory, "confirmed", {}).get(RETRIEVAL_HISTORY_STOP_CONFIRMED_KEY) or "").strip()
    streak = _phase_metric(phase_memory, RETRIEVAL_HISTORY_STOP_STREAK_KEY)
    if confirmed and streak >= 2:
        return RetrievalPaginationGate(
            allowed=False,
            message="Two recorded retrieval pages reached the history-stop threshold. Do not paginate again; finish retrieval unless the active site Skill has a stricter unmet requirement.",
        )
    return RetrievalPaginationGate(allowed=True)


def is_pagination_action(tool_name: str, arguments: dict[str, Any] | None = None) -> bool:
    """Recognize generic next/load-more requests without interpreting a website."""

    normalized_name = str(tool_name or "").strip().lower()
    if normalized_name not in {"browser_click", "browser_run_code"}:
        return False
    text = " ".join(str(value or "") for value in (arguments or {}).values()).lower()
    return any(marker in text for marker in ("next", "pagination", "load more", "show more", "more results"))


def _phase_metric(phase_memory: Any, key: str) -> int:
    getter = getattr(phase_memory, "get_metric", None)
    if not callable(getter):
        return 0
    try:
        return max(0, int(getter(key) or 0))
    except (TypeError, ValueError):
        return 0
