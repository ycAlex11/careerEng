"""Shared guards for business state, independent of runtime cleanup."""

TERMINAL_WORK_STATES = frozenset({"completed", "partial_completed", "cancelled", "failed", "done", "skipped", "released", "stopped"})


def is_terminal_work(state: str) -> bool:
    return str(state or "").strip() in TERMINAL_WORK_STATES
