"""Mechanical cross-batch continuity decisions for flat native workers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .actions import WorkerActionKind


class WorkerContinuityMode(StrEnum):
    SPAWN = "spawn"
    SEND = "send"
    RESUME_AND_SEND = "resume_and_send"


@dataclass(frozen=True)
class WorkerContinuityPlan:
    mode: WorkerContinuityMode
    agent_id: str = ""
    replacement_reason: str = ""

    @property
    def action_kinds(self) -> tuple[WorkerActionKind, ...]:
        if self.mode == WorkerContinuityMode.SEND:
            return (WorkerActionKind.SEND,)
        if self.mode == WorkerContinuityMode.RESUME_AND_SEND:
            return (WorkerActionKind.RESUME, WorkerActionKind.SEND)
        return (WorkerActionKind.SPAWN,)


def plan_worker_continuity(*, bound_agent_id: str, previous_worker: dict[str, Any] | None) -> WorkerContinuityPlan:
    """Choose transport actions without interpreting site or workflow content."""

    agent_id = str(bound_agent_id or "").strip()
    if not agent_id:
        return WorkerContinuityPlan(WorkerContinuityMode.SPAWN)
    previous = previous_worker if isinstance(previous_worker, dict) else {}
    if not previous or str(previous.get("agent_id") or "") != agent_id:
        return WorkerContinuityPlan(
            WorkerContinuityMode.SPAWN,
            replacement_reason="bound_agent_not_registered",
        )
    runtime_state = str(previous.get("runtime_state") or "detached")
    if runtime_state in {"running", "starting", "quiescing"}:
        return WorkerContinuityPlan(WorkerContinuityMode.SEND, agent_id=agent_id)
    if runtime_state == "suspended":
        return WorkerContinuityPlan(WorkerContinuityMode.RESUME_AND_SEND, agent_id=agent_id)
    return WorkerContinuityPlan(
        WorkerContinuityMode.SPAWN,
        replacement_reason=f"bound_agent_{runtime_state or 'unknown'}",
    )
