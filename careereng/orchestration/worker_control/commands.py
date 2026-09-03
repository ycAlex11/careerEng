"""Durable command envelopes for asynchronous worker control."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from careereng.utils import make_id, now_iso


class WorkerCommandKind(StrEnum):
    GUIDANCE = "guidance"
    REDIRECT = "redirect"
    RESUME = "resume"
    PAUSE = "pause"
    CANCEL = "cancel"
    RECOVERY = "recovery"


class WorkerCommandDelivery(StrEnum):
    NEXT_BOUNDARY = "next_boundary"
    INTERRUPT_THEN_REPLACE = "interrupt_then_replace"
    ACTIVATE = "activate"
    TERMINATE = "terminate"


class WorkerCommandStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    APPLIED = "applied"
    SUPERSEDED = "superseded"
    FAILED = "failed"


_DEFAULT_DELIVERY = {
    WorkerCommandKind.GUIDANCE: WorkerCommandDelivery.NEXT_BOUNDARY,
    WorkerCommandKind.REDIRECT: WorkerCommandDelivery.INTERRUPT_THEN_REPLACE,
    WorkerCommandKind.RESUME: WorkerCommandDelivery.ACTIVATE,
    WorkerCommandKind.PAUSE: WorkerCommandDelivery.INTERRUPT_THEN_REPLACE,
    WorkerCommandKind.CANCEL: WorkerCommandDelivery.TERMINATE,
    WorkerCommandKind.RECOVERY: WorkerCommandDelivery.INTERRUPT_THEN_REPLACE,
}


@dataclass(frozen=True)
class WorkerCommand:
    command_id: str
    site_key: str
    batch_id: str
    work_item_id: str
    kind: WorkerCommandKind
    message: str
    delivery: WorkerCommandDelivery
    status: WorkerCommandStatus = WorkerCommandStatus.PENDING
    sequence: int = 0
    expected_control_epoch: int = 0
    expected_context_revision: int = 0
    created_at: str = ""
    updated_at: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "site_key": self.site_key,
            "batch_id": self.batch_id,
            "work_item_id": self.work_item_id,
            "kind": self.kind.value,
            "message": self.message,
            "delivery": self.delivery.value,
            "status": self.status.value,
            "sequence": self.sequence,
            "expected_control_epoch": self.expected_control_epoch,
            "expected_context_revision": self.expected_context_revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorkerCommand":
        return cls(
            command_id=str(payload.get("command_id") or ""),
            site_key=str(payload.get("site_key") or ""),
            batch_id=str(payload.get("batch_id") or ""),
            work_item_id=str(payload.get("work_item_id") or ""),
            kind=WorkerCommandKind(str(payload.get("kind") or WorkerCommandKind.GUIDANCE.value)),
            message=str(payload.get("message") or ""),
            delivery=WorkerCommandDelivery(
                str(payload.get("delivery") or WorkerCommandDelivery.NEXT_BOUNDARY.value)
            ),
            status=WorkerCommandStatus(str(payload.get("status") or WorkerCommandStatus.PENDING.value)),
            sequence=max(0, int(payload.get("sequence") or 0)),
            expected_control_epoch=max(0, int(payload.get("expected_control_epoch") or 0)),
            expected_context_revision=max(0, int(payload.get("expected_context_revision") or 0)),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            error=str(payload.get("error") or ""),
        )


def create_worker_command(
    *,
    site_key: str,
    batch_id: str,
    work_item_id: str,
    kind: WorkerCommandKind | str,
    message: str = "",
    command_id: str = "",
    delivery: WorkerCommandDelivery | str | None = None,
    expected_control_epoch: int = 0,
    expected_context_revision: int = 0,
) -> WorkerCommand:
    normalized_kind = kind if isinstance(kind, WorkerCommandKind) else WorkerCommandKind(str(kind))
    normalized_delivery = delivery or _DEFAULT_DELIVERY[normalized_kind]
    if not isinstance(normalized_delivery, WorkerCommandDelivery):
        normalized_delivery = WorkerCommandDelivery(str(normalized_delivery))
    normalized_site = str(site_key or "").strip()
    normalized_batch = str(batch_id or "").strip()
    normalized_work_item = str(work_item_id or "").strip()
    if not normalized_site or not normalized_batch or not normalized_work_item:
        raise ValueError("worker command requires site, batch, and work-item scope")
    now = now_iso()
    return WorkerCommand(
        command_id=str(command_id or make_id("worker_command")),
        site_key=normalized_site,
        batch_id=normalized_batch,
        work_item_id=normalized_work_item,
        kind=normalized_kind,
        message=str(message or "").strip(),
        delivery=normalized_delivery,
        expected_control_epoch=max(0, int(expected_control_epoch or 0)),
        expected_context_revision=max(0, int(expected_context_revision or 0)),
        created_at=now,
        updated_at=now,
    )
