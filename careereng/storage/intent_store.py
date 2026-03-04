"""Intent domain storage."""

from __future__ import annotations

from pathlib import Path

from careereng.storage.domain_store import DomainStore


DEFAULT_INTENT = {
    "version": 1,
    "updated_at": "2026-03-01",
    "target_roles": [],
    "target_locations": ["China"],
    "location_note": "Any city in China is acceptable",
    "work_mode": "",
    "employment_type": "",
    "company_preferences": [],
    "industry_preferences": [],
    "date_posted_after": "",
    "must_have": [],
    "nice_to_have": [],
}


class IntentStore(DomainStore):
    def __init__(self, workspace: Path):
        super().__init__(
            workspace,
            domain="intent",
            doc_name="intent.md",
            events_name="intent_events.jsonl",
            default_doc=DEFAULT_INTENT,
        )
