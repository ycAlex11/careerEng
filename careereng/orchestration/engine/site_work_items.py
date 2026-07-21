"""Generic per-site work-item scheduling.

The scheduler only owns queue/slot semantics.  Adapters decide how an item is
executed (Codex thread today); CareerEng domains retain ownership of the data
and workflow represented by each item.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SiteWorkItem:
    site_key: str
    batch_id: str
    payload: Any


class SiteWorkItemScheduler:
    """Bound concurrent site work while preserving one active item per site."""

    def __init__(self, *, worker_limit: int):
        self.worker_limit = max(1, int(worker_limit or 1))
        self._queued: deque[SiteWorkItem] = deque()
        self._active: dict[str, SiteWorkItem] = {}

    def enqueue(self, item: SiteWorkItem) -> bool:
        if item.site_key in self._active or any(row.site_key == item.site_key for row in self._queued):
            return False
        self._queued.append(item)
        return True

    def claim_ready(self) -> list[SiteWorkItem]:
        ready: list[SiteWorkItem] = []
        while self._queued and len(self._active) < self.worker_limit:
            item = self._queued.popleft()
            self._active[item.site_key] = item
            ready.append(item)
        return ready

    def complete(self, site_key: str) -> SiteWorkItem | None:
        return self._active.pop(str(site_key), None)

    def active(self, site_key: str) -> SiteWorkItem | None:
        return self._active.get(str(site_key))

    def snapshot(self) -> dict[str, list[SiteWorkItem]]:
        return {"active": list(self._active.values()), "queued": list(self._queued)}
