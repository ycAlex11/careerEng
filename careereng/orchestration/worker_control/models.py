"""Control-plane models shared by worker coordinators and tool gateways."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class WorkerControlState(StrEnum):
    ACTIVE = "active"
    TRANSITIONING = "transitioning"
    WAITING_USER = "waiting_user"
    PAUSING = "pausing"
    PAUSED = "paused"
    PAUSE_UNCONFIRMED = "pause_unconfirmed"
    STOPPING = "stopping"
    STOPPED = "stopped"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    RELEASED = "released"


@dataclass(frozen=True)
class WorkerControlVersion:
    control_epoch: int
    site_revision: int
