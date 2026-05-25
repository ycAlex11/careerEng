"""Persistent stores for evolution review artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.storage.jsonl import JSONLStore
from careereng.utils import ensure_dir, today_str, write_json


class EvolutionStore:
    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.root = ensure_dir(self.workspace / "evolution")
        self.evidence_dir = ensure_dir(self.root / "evidence")
        self.candidates_dir = ensure_dir(self.root / "candidates")
        self.memory_dir = ensure_dir(self.root / "memory")
        self.reviews_dir = ensure_dir(self.root / "reviews")
        self.context_dir = ensure_dir(self.root / "context")
        self.evidence_store = JSONLStore(self.evidence_dir / "all.jsonl")
        self.open_candidates_store = JSONLStore(self.candidates_dir / "open.jsonl")
        self.memory_units_store = JSONLStore(self.memory_dir / "units.jsonl")

    def upsert_evidence(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._upsert_jsonl(self.evidence_store, rows, key="evidence_id")

    def upsert_open_candidates(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._upsert_jsonl(self.open_candidates_store, rows, key="candidate_id")

    def upsert_memory_units(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._upsert_jsonl(self.memory_units_store, rows, key="memory_id")

    def save_review_json(self, review: dict[str, Any], *, date_label: str | None = None) -> Path:
        date_text = date_label or today_str()
        path = self.reviews_dir / f"{date_text}.json"
        write_json(path, review)
        return path

    def save_review_markdown(self, text: str, *, date_label: str | None = None) -> Path:
        date_text = date_label or today_str()
        path = self.reviews_dir / f"{date_text}.md"
        ensure_dir(path.parent)
        path.write_text(text.rstrip() + "\n", encoding="utf-8")
        return path

    def save_context_markdown(self, text: str) -> Path:
        path = self.context_dir / "latest.md"
        ensure_dir(path.parent)
        path.write_text(text.rstrip() + "\n", encoding="utf-8")
        return path

    @staticmethod
    def _upsert_jsonl(store: JSONLStore, rows: list[dict[str, Any]], *, key: str) -> list[dict[str, Any]]:
        existing = store.read_all()
        by_key: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for row in existing:
            row_key = str(row.get(key) or "").strip()
            if not row_key:
                continue
            if row_key not in by_key:
                order.append(row_key)
            by_key[row_key] = row
        for row in rows:
            row_key = str(row.get(key) or "").strip()
            if not row_key:
                continue
            current = by_key.get(row_key)
            if current:
                merged = {**current, **row}
                if current.get("created_at"):
                    merged["created_at"] = current["created_at"]
                by_key[row_key] = merged
            else:
                order.append(row_key)
                by_key[row_key] = row
        merged_rows = [by_key[item] for item in order if item in by_key]
        store.write_all(merged_rows)
        return merged_rows
