"""Batch-scoped history rows and identity indexes for application planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from careereng.platform.persistence import RevisionedStore, RunScopedView


HistoryRowsLoader = Callable[[], list[dict[str, Any]]]
HistoryIndexesBuilder = Callable[[list[dict[str, Any]]], tuple[dict[str, int], dict[str, int], dict[str, int]]]


@dataclass
class BatchHistoryView:
    """A domain view that owns no durable data and makes no match decisions."""

    site_key: str
    batch_id: str
    rows_view: RunScopedView[list[dict[str, Any]]]
    indexes_builder: HistoryIndexesBuilder
    _indexes: tuple[dict[str, int], dict[str, int], dict[str, int]] | None = field(default=None, init=False)

    @classmethod
    def create(
        cls,
        *,
        site_key: str,
        batch_id: str,
        history_path: str,
        loader: HistoryRowsLoader,
        indexes_builder: HistoryIndexesBuilder,
    ) -> "BatchHistoryView":
        return cls(
            site_key=str(site_key),
            batch_id=str(batch_id),
            rows_view=RunScopedView(store=RevisionedStore(history_path), loader=loader),
            indexes_builder=indexes_builder,
        )

    def rows(self) -> list[dict[str, Any]]:
        before = self.rows_view.revision
        rows = self.rows_view.get()
        if before != self.rows_view.revision:
            self._indexes = None
        return rows

    def indexes(self) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
        rows = self.rows()
        if self._indexes is None:
            self._indexes = self.indexes_builder(rows)
        return self._indexes

    def replace_after_write(self, rows: list[dict[str, Any]]) -> None:
        self.rows_view.replace([dict(row) for row in rows if isinstance(row, dict)])
        self._indexes = None

    def invalidate(self) -> None:
        self.rows_view.invalidate()
        self._indexes = None
