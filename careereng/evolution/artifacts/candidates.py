"""Persistence owner for open evolution candidates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.evolution.artifacts._jsonl import upsert_rows
from careereng.evolution.artifacts.paths import open_candidates_path
from careereng.platform.persistence import JSONLStore
from careereng.utils import ensure_dir


class OpenEvolutionCandidateStore:
    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.path = open_candidates_path(self.workspace)
        ensure_dir(self.path.parent)
        self.store = JSONLStore(self.path)

    def read_all(self) -> list[dict[str, Any]]:
        return self.store.read_all()

    def append(self, row: dict[str, Any]) -> None:
        self.store.append(row)

    def upsert_many(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return upsert_rows(self.store, rows, key="candidate_id")
