"""Control-plane models shared by worker coordinators and tool gateways."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class WorkerDesiredState(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class AgentRuntimeState(StrEnum):
    DETACHED = "detached"
    STARTING = "starting"
    RUNNING = "running"
    QUIESCING = "quiescing"
    SUSPENDED = "suspended"
    TERMINAL = "terminal"
    FAULTED = "faulted"


class WorkItemRuntimeState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BrowserResourceState(StrEnum):
    ABSENT = "absent"
    STARTING = "starting"
    READY = "ready"
    RETAINED = "retained"
    RELEASING = "releasing"
    LOST = "lost"


class BrowserResourcePolicy(StrEnum):
    UNCHANGED = "unchanged"
    RETAIN = "retain"
    RELEASE = "release"
    RESTORE = "restore"


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


@dataclass(frozen=True)
class WorkerLifecycleSnapshot:
    agent_id: str
    work_item_id: str
    site_key: str
    batch_id: str
    worker_kind: str
    desired_state: WorkerDesiredState
    runtime_state: AgentRuntimeState
    work_state: WorkItemRuntimeState
    browser_state: BrowserResourceState
    control_epoch: int = 0
    revision: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "work_item_id": self.work_item_id,
            "site_key": self.site_key,
            "batch_id": self.batch_id,
            "worker_kind": self.worker_kind,
            "desired_state": self.desired_state.value,
            "runtime_state": self.runtime_state.value,
            "work_state": self.work_state.value,
            "browser_state": self.browser_state.value,
            "control_epoch": self.control_epoch,
            "revision": self.revision,
        }
