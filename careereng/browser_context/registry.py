"""Preloaded browser-phase context resources."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from careereng.storage.cv_store import CVStore
from careereng.storage.profile_store import ProfileStore


class BrowserContextRegistry:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.profile_store = ProfileStore(self.workspace)
        self.cv_store = CVStore(self.workspace)
        self.persona_doc: dict[str, Any] = {}
        self.cv_text: str = ""
        self.apply_facts: dict[str, Any] = {}
        self.refresh()

    def refresh(self) -> None:
        try:
            persona = self.profile_store.load_doc()
        except Exception:
            persona = {}
        self.persona_doc = persona if isinstance(persona, dict) else {}
        try:
            cv_text = self.cv_store.load_current_text()
        except Exception:
            cv_text = ""
        self.cv_text = str(cv_text or "").strip()
        self.apply_facts = self._build_apply_facts(self.persona_doc)

    @staticmethod
    def _prune(value: Any) -> Any:
        if isinstance(value, dict):
            cleaned: dict[str, Any] = {}
            for key, raw in value.items():
                item = BrowserContextRegistry._prune(raw)
                if item in ("", [], {}, None):
                    continue
                cleaned[str(key)] = item
            return cleaned
        if isinstance(value, list):
            cleaned_list = [BrowserContextRegistry._prune(item) for item in value]
            return [item for item in cleaned_list if item not in ("", [], {}, None)]
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return value

    @classmethod
    def _build_apply_facts(cls, persona_doc: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(persona_doc, dict):
            return {}
        facts: dict[str, Any] = {}
        for key in ("basic", "contact", "constraints", "summary"):
            raw = persona_doc.get(key)
            cleaned = cls._prune(raw)
            if cleaned not in ("", [], {}, None):
                facts[key] = cleaned
        for key in ("name", "email", "phone", "location", "nationality", "gender", "work_auth", "visa"):
            raw = persona_doc.get(key)
            cleaned = cls._prune(raw)
            if cleaned not in ("", [], {}, None):
                facts[key] = cleaned
        return facts

    def available_bundles(self) -> list[str]:
        bundles: list[str] = []
        if self.apply_facts:
            bundles.append("apply_facts")
        if self.cv_text:
            bundles.append("full_cv")
        if self.persona_doc:
            bundles.append("full_persona")
        return bundles

    def has_bundle(self, bundle: str) -> bool:
        return str(bundle or "").strip().lower() in set(self.available_bundles())

    def bundle_item_text(self, bundle: str) -> str:
        normalized = str(bundle or "").strip().lower()
        if normalized == "apply_facts":
            return (
                "Current lightweight apply facts:\n"
                + json.dumps(self.apply_facts, ensure_ascii=False, indent=2)
            )
        if normalized == "full_cv":
            return f"Full CV text (requested bundle `full_cv`):\n{self.cv_text}"
        if normalized == "full_persona":
            return (
                "Full persona profile data (requested bundle `full_persona`):\n"
                + json.dumps(self.persona_doc, ensure_ascii=False, indent=2)
            )
        return ""

    def available_bundles_item_text(self) -> str:
        available = self.available_bundles()
        lines = [
            "Additional context bundles are available through `request_context` when the current live page and currently attached context are insufficient.",
        ]
        if "apply_facts" in available:
            lines.append("- `apply_facts`: current lightweight structured profile facts for routine form filling.")
        if "full_cv" in available:
            lines.append("- `full_cv`: current full CV text for detailed experience or open-ended answers.")
        if "full_persona" in available:
            lines.append("- `full_persona`: current full persona profile data for detailed background and constraints.")
        lines.append("Do not request a fuller bundle unless the active page or active rule actually needs more detail.")
        return "\n".join(lines)
