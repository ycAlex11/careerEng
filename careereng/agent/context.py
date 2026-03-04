"""Prompt context assembly."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ContextBuilder:
    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md"]

    def __init__(self, workspace: Path):
        self.workspace = workspace

    def _bootstrap_text(self) -> str:
        parts = []
        for name in self.BOOTSTRAP_FILES:
            path = self.workspace / name
            if path.exists():
                parts.append(f"## {name}\n{path.read_text(encoding='utf-8').strip()}")
        return "\n\n".join(parts)

    def build_messages(
        self,
        *,
        session_history: list[dict[str, str]],
        user_message: str,
        persona: dict[str, Any],
        intent: dict[str, Any],
        relatedness: dict[str, Any],
        profile_related_history: list[dict[str, Any]],
        intent_related_history: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        parts = [
            "You are CareerEng, a concise assistant for job search and application automation.",
            "If user asks for site search/apply, provide clear next actions.",
        ]

        bootstrap = self._bootstrap_text()
        if bootstrap:
            parts.append(bootstrap)

        if relatedness.get("is_profile_related"):
            parts.append("[persona.md context]\n" + json.dumps(persona, ensure_ascii=False, indent=2))
            if profile_related_history:
                lines = []
                for row in profile_related_history:
                    lines.append(f"- {row.get('role')}: {row.get('content')}")
                parts.append("[recent profile-related history]\n" + "\n".join(lines))

        if relatedness.get("is_intent_related"):
            parts.append("[intent.md context]\n" + json.dumps(intent, ensure_ascii=False, indent=2))
            if intent_related_history:
                lines = []
                for row in intent_related_history:
                    lines.append(f"- {row.get('role')}: {row.get('content')}")
                parts.append("[recent intent-related history]\n" + "\n".join(lines))

        system_prompt = "\n\n".join(parts)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(session_history)
        messages.append({"role": "user", "content": user_message})
        return messages
