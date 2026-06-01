"""Shared review pack schema."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ReviewPack:
    review_id: str
    created_at: str
    review_type: str
    subject_id: str
    subject_ref: str
    status: str = "needs_codex_review"
    recommended_status: str = "needs_codex_review"
    codex_review_required: bool = True
    metrics: dict[str, Any] = field(default_factory=dict)
    sections: list[dict[str, Any]] = field(default_factory=list)
    sample_rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    review_questions: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    pack_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
