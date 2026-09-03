"""Pure arbitration rules for commands targeting one serialized worker."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .commands import WorkerCommand, WorkerCommandKind


class WorkerCommandAction(StrEnum):
    QUEUE = "queue"
    START = "start"
    INTERRUPT = "interrupt"
    TERMINATE = "terminate"


@dataclass(frozen=True)
class WorkerCommandDecision:
    action: WorkerCommandAction
    reason: str


class WorkerCommandArbiter:
    def decide(
        self,
        command: WorkerCommand,
        *,
        worker_status: str,
        has_turn: bool,
        turn_start_inflight: bool,
        recovery_pending: bool,
    ) -> WorkerCommandDecision:
        if command.kind == WorkerCommandKind.CANCEL:
            return WorkerCommandDecision(
                WorkerCommandAction.INTERRUPT if has_turn else WorkerCommandAction.TERMINATE,
                "cancel revokes the current worker scope",
            )
        if command.kind == WorkerCommandKind.PAUSE:
            return WorkerCommandDecision(
                WorkerCommandAction.INTERRUPT if has_turn else WorkerCommandAction.TERMINATE,
                "pause waits for the current turn boundary",
            )
        if turn_start_inflight or worker_status in {"starting", "pausing"}:
            return WorkerCommandDecision(WorkerCommandAction.QUEUE, "worker control transition is in flight")
        if recovery_pending and command.kind != WorkerCommandKind.RECOVERY:
            return WorkerCommandDecision(WorkerCommandAction.QUEUE, "user command owns the pending recovery boundary")
        if command.kind == WorkerCommandKind.RECOVERY and recovery_pending:
            return WorkerCommandDecision(WorkerCommandAction.QUEUE, "recovery is already pending")
        if command.kind == WorkerCommandKind.REDIRECT:
            return WorkerCommandDecision(
                WorkerCommandAction.INTERRUPT if has_turn else WorkerCommandAction.START,
                "redirect replaces work only after the current turn ends",
            )
        if has_turn:
            return WorkerCommandDecision(WorkerCommandAction.QUEUE, "deliver at the next safe turn boundary")
        return WorkerCommandDecision(WorkerCommandAction.START, "worker has no in-flight turn")
