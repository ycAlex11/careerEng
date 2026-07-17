"""Compatibility facade for evolution artifact persistence.

New code should use the specialized stores in ``evolution.artifacts``. This
facade preserves the historical review-store API while callers migrate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.evolution.artifacts import (
    EvolutionContextStore,
    EvolutionEvidenceStore,
    EvolutionReviewStore,
    OpenEvolutionCandidateStore,
)
from careereng.evolution.memory_units import EvolutionMemoryStore


class EvolutionStore:
    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.evidence = EvolutionEvidenceStore(self.workspace)
        self.candidates = OpenEvolutionCandidateStore(self.workspace)
        self.memory = EvolutionMemoryStore(self.workspace)
        self.reviews = EvolutionReviewStore(self.workspace)
        self.context = EvolutionContextStore(self.workspace)

        # Legacy attributes preserve the established API during migration.
        self.root = self.evidence.path.parent.parent
        self.evidence_dir = self.evidence.path.parent
        self.candidates_dir = self.candidates.path.parent
        self.memory_dir = self.memory.store.path.parent
        self.reviews_dir = self.reviews.root
        self.context_dir = self.context.path.parent
        self.evidence_store = self.evidence.store
        self.open_candidates_store = self.candidates.store
        self.memory_units_store = self.memory.store

    def upsert_evidence(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.evidence.upsert_many(rows)

    def upsert_open_candidates(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.candidates.upsert_many(rows)

    def upsert_memory_units(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.memory.upsert_many(rows)

    def save_review_json(self, review: dict[str, Any], *, date_label: str | None = None) -> Path:
        return self.reviews.save_json(review, date_label=date_label)

    def save_review_markdown(self, text: str, *, date_label: str | None = None) -> Path:
        return self.reviews.save_markdown(text, date_label=date_label)

    def save_context_markdown(self, text: str) -> Path:
        return self.context.save_markdown(text)
