"""Validation for immutable work-item execution leases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .transitions import can_execute


@dataclass(frozen=True)
class WorkItemFence:
    work_item_id: str
    site_key: str
    batch_id: str
    control_epoch: int
    site_revision: int

    def as_payload(self) -> dict[str, Any]:
        return {
            "work_item_id": self.work_item_id,
            "site_key": self.site_key,
            "batch_id": self.batch_id,
            "control_epoch": self.control_epoch,
            "site_revision": self.site_revision,
        }


def validate_work_item_fence(
    record: dict[str, Any],
    fence: WorkItemFence,
    *,
    require_revision: bool = True,
) -> None:
    if str(record.get("work_item_id") or "") != fence.work_item_id:
        raise ValueError("work item fence does not match the active item")
    if str(record.get("site_key") or "") != fence.site_key:
        raise ValueError("work item fence does not match the active site")
    if str(record.get("batch_id") or "") != fence.batch_id:
        raise ValueError("work item fence does not match the active batch")
    if int(record.get("control_epoch") or 0) != int(fence.control_epoch):
        raise ValueError("work item execution lease has been revoked")
    if require_revision and int(record.get("site_revision") or 0) != int(fence.site_revision):
        raise ValueError("work item state revision is stale")
    if not can_execute(record.get("state")):
        raise ValueError(f"work item is not executable: {record.get('state') or 'unknown'}")
