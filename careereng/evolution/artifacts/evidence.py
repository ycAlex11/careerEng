"""Persistence owner for immutable-ish evolution evidence rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.evolution.artifacts._jsonl import upsert_rows
from careereng.evolution.artifacts.paths import evidence_path
from careereng.platform.persistence import JSONLStore
from careereng.utils import ensure_dir


class EvolutionEvidenceStore:
    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.path = evidence_path(self.workspace)
        ensure_dir(self.path.parent)
        self.store = JSONLStore(self.path)

    def read_all(self) -> list[dict[str, Any]]:
        return self.store.read_all()

    def upsert_many(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return upsert_rows(self.store, rows, key="evidence_id")
