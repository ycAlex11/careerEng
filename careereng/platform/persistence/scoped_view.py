"""Run-scoped cached views over durable workspace data."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from .revisioned_store import FileRevision, RevisionedStore


T = TypeVar("T")


@dataclass
class RunScopedView(Generic[T]):
    """Cache one loaded value until its backing file revision changes."""

    store: RevisionedStore
    loader: Callable[[], T]
    value: T | None = None
    revision: FileRevision | None = None

    def get(self) -> T:
        if self.value is None or self.store.changed_since(self.revision):
            self.refresh()
        return self.value

    def refresh(self) -> T:
        self.value = self.loader()
        self.revision = self.store.revision()
        return self.value

    def replace(self, value: T) -> T:
        """Synchronize a caller-confirmed durable write without reloading it."""

        self.value = value
        self.revision = self.store.revision()
        return value

    def invalidate(self) -> None:
        self.value = None
        self.revision = None
