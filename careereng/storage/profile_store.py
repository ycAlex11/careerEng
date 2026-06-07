"""Profile domain storage."""

from __future__ import annotations

from pathlib import Path

from careereng.storage.domain_store import DomainStore


DEFAULT_PERSONA = {
    "version": 1,
    "updated_at": "2026-03-01",
    "basic": {
        "name": "",
        "nationality": "China",
        "current_city": "Taiyuan",
        "languages": ["中文", "English"],
    },
    "contact": {
        "address": {
            "country": "China",
            "state_province": "Shanxi",
            "city_town": "Taiyuan",
            "postal_code": "030000",
        },
    },
    "education": [],
    "experience": [],
    "projects": [],
    "skills": {
        "programming": [],
        "frameworks": [],
        "tools": [],
        "ai": [],
    },
    "summary": {
        "profile": "",
        "work_style": "",
    },
    "constraints": {
        "visa": "none",
        "work_auth": "china",
    },
}


class ProfileStore(DomainStore):
    def __init__(self, workspace: Path):
        super().__init__(
            workspace,
            domain="profile",
            doc_name="persona.md",
            events_name="profile_events.jsonl",
            default_doc=DEFAULT_PERSONA,
        )
