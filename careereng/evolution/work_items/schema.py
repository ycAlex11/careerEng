"""Action-card schema."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


ACTION_CARD_OPEN = "open"
ACTION_CARD_DONE = "done"
ACTION_CARD_CANCELLED = "cancelled"
ACTION_CARD_STATUSES = {ACTION_CARD_OPEN, ACTION_CARD_DONE, ACTION_CARD_CANCELLED}

ACTION_CARD_CODEX_REVIEW = "codex_review"
ACTION_CARD_CODEX_DRAFT = "codex_draft"
ACTION_CARD_CODEX_DEBUG = "codex_debug"
ACTION_CARD_HUMAN_ACTION = "human_action"
ACTION_CARD_MANUAL_DECISION = "manual_decision"
ACTION_CARD_COLLABORATION_TYPES = {
    ACTION_CARD_CODEX_REVIEW,
    ACTION_CARD_CODEX_DRAFT,
    ACTION_CARD_CODEX_DEBUG,
    ACTION_CARD_HUMAN_ACTION,
    ACTION_CARD_MANUAL_DECISION,
}

__all__ = [
    "ACTION_CARD_CANCELLED",
    "ACTION_CARD_CODEX_DEBUG",
    "ACTION_CARD_CODEX_DRAFT",
    "ACTION_CARD_CODEX_REVIEW",
    "ACTION_CARD_COLLABORATION_TYPES",
    "ACTION_CARD_DONE",
    "ACTION_CARD_HUMAN_ACTION",
    "ACTION_CARD_MANUAL_DECISION",
    "ACTION_CARD_OPEN",
    "ACTION_CARD_STATUSES",
    "ActionCard",
]


@dataclass
class ActionCard:
    card_id: str
    created_at: str
    updated_at: str
    status: str
    card_type: str
    title: str
    goal: str
    reason: str = ""
    source_type: str = ""
    source_id: str = ""
    source_ref: str = ""
    priority: str = "medium"
    related_files: list[str] = field(default_factory=list)
    suggested_actions: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    safety_notes: list[str] = field(default_factory=list)
    done_when: list[str] = field(default_factory=list)
    result_summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    semantic_tags: list[str] = field(default_factory=list)
    markdown_path: str = ""
    dedupe_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
