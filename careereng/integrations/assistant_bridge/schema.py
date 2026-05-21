"""Shared schemas and constants for the assistant bridge."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


DATA_CATEGORY_CAREERENG_COMMAND = "careereng_command"
DATA_CATEGORY_PROFILE_RESUME_SIGNAL = "profile_resume_signal"
DATA_CATEGORY_CAREER_INTENT_STRATEGY = "career_intent_strategy"
DATA_CATEGORY_APPLICATION_FEEDBACK = "application_feedback"
DATA_CATEGORY_CORRECTION = "correction"
DATA_CATEGORY_INTERVIEW_RECORD = "interview_record"
DATA_CATEGORY_CHAT = "chat"

DATA_CATEGORIES = {
    DATA_CATEGORY_CAREERENG_COMMAND,
    DATA_CATEGORY_PROFILE_RESUME_SIGNAL,
    DATA_CATEGORY_CAREER_INTENT_STRATEGY,
    DATA_CATEGORY_APPLICATION_FEEDBACK,
    DATA_CATEGORY_CORRECTION,
    DATA_CATEGORY_INTERVIEW_RECORD,
    DATA_CATEGORY_CHAT,
}

SCOPED_DATA_CATEGORIES = {
    DATA_CATEGORY_PROFILE_RESUME_SIGNAL,
    DATA_CATEGORY_CAREER_INTENT_STRATEGY,
    DATA_CATEGORY_APPLICATION_FEEDBACK,
    DATA_CATEGORY_INTERVIEW_RECORD,
}

TRIGGER_EXPLICIT = "explicit"
TRIGGER_SCOPE_FOLLOWUP = "scope_followup"
TRIGGER_IMPLICIT_SUGGESTED = "implicit_suggested"
TRIGGER_NONE = "none"


@dataclass
class AssistantBridgeDecision:
    data_category: str = DATA_CATEGORY_CHAT
    route: str = "chat"
    confidence: float = 0.0
    semantic_labels: list[str] = field(default_factory=list)
    detected_entities: dict[str, Any] = field(default_factory=dict)
    suggested_action: str = ""
    suggested_command: str = ""
    should_save: bool = False
    should_execute: bool = False
    reason: str = ""
    processor_backend: str = "local"
    processor_version: str = "v1"
    processor_trace: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["confidence"] = float(data.get("confidence") or 0.0)
        return data
