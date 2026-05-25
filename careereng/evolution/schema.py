"""Shared schemas for review-driven evolution."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class EvolutionEvidence:
    evidence_id: str
    created_at: str
    source_type: str
    source_ref: str
    area: str
    site_key: str = ""
    phase: str = ""
    event_type: str = ""
    severity: str = "info"
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    entities: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImprovementCandidate:
    candidate_id: str
    created_at: str
    updated_at: str
    area: str
    target_type: str
    target_ref: str
    site_key: str = ""
    phase: str = ""
    priority: str = "medium"
    status: str = "open"
    summary: str = ""
    suggested_change: str = ""
    reason: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    evidence_count: int = 0
    risk: str = "medium"
    owner: str = "human"
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryUnit:
    memory_id: str
    created_at: str
    updated_at: str
    memory_type: str
    status: str
    summary: str
    content: str
    entities: dict[str, Any] = field(default_factory=dict)
    labels: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    confidence: float = 0.0
    supersedes: list[str] = field(default_factory=list)
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["confidence"] = float(data.get("confidence") or 0.0)
        return data
