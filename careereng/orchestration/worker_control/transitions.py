"""Pure worker-control transition helpers."""

from __future__ import annotations

from .models import WorkerControlState


EXECUTABLE_STATES = {WorkerControlState.ACTIVE.value}
TERMINAL_STATES = {
    WorkerControlState.STOPPED.value,
    WorkerControlState.CANCELLED.value,
    WorkerControlState.COMPLETED.value,
    WorkerControlState.RELEASED.value,
}

_CONTROL_TRANSITIONS = {
    WorkerControlState.TRANSITIONING.value: {
        WorkerControlState.ACTIVE.value,
        WorkerControlState.WAITING_USER.value,
        WorkerControlState.COMPLETED.value,
        WorkerControlState.STOPPING.value,
        WorkerControlState.CANCELLING.value,
        WorkerControlState.CANCELLED.value,
        WorkerControlState.RELEASED.value,
    },
    WorkerControlState.WAITING_USER.value: {
        WorkerControlState.STOPPING.value,
        WorkerControlState.CANCELLING.value,
        WorkerControlState.CANCELLED.value,
        WorkerControlState.RELEASED.value,
    },
    WorkerControlState.PAUSING.value: {
        WorkerControlState.PAUSED.value,
        WorkerControlState.PAUSE_UNCONFIRMED.value,
        WorkerControlState.STOPPING.value,
        WorkerControlState.CANCELLING.value,
        WorkerControlState.CANCELLED.value,
        WorkerControlState.RELEASED.value,
    },
    WorkerControlState.PAUSED.value: {
        WorkerControlState.STOPPING.value,
        WorkerControlState.CANCELLING.value,
        WorkerControlState.CANCELLED.value,
        WorkerControlState.RELEASED.value,
    },
    WorkerControlState.PAUSE_UNCONFIRMED.value: {
        WorkerControlState.STOPPING.value,
        WorkerControlState.CANCELLING.value,
        WorkerControlState.CANCELLED.value,
        WorkerControlState.RELEASED.value,
    },
    WorkerControlState.STOPPING.value: {
        WorkerControlState.STOPPED.value,
        WorkerControlState.CANCELLING.value,
        WorkerControlState.CANCELLED.value,
        WorkerControlState.RELEASED.value,
    },
    WorkerControlState.CANCELLING.value: {
        WorkerControlState.CANCELLED.value,
        WorkerControlState.RELEASED.value,
    },
}


def can_execute(state: object) -> bool:
    return str(state or "").strip() in EXECUTABLE_STATES


def can_transition(current: object, requested: object) -> bool:
    current_state = str(current or "").strip()
    requested_state = str(requested or "").strip()
    if not requested_state or current_state == requested_state:
        return True
    if not current_state or current_state == WorkerControlState.ACTIVE.value:
        return True
    if current_state in TERMINAL_STATES:
        return False
    return requested_state in _CONTROL_TRANSITIONS.get(current_state, set())


def next_control_epoch(value: object) -> int:
    try:
        current = int(value or 0)
    except (TypeError, ValueError):
        current = 0
    return max(0, current) + 1
