"""Preloaded browser-phase context resources."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from careereng.career.profile.store import ProfileStore
from careereng.career.resume.store import CVStore
from careereng.utils import parse_front_matter, read_json


class BrowserContextRegistry:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.profile_store = ProfileStore(self.workspace)
        self.cv_store = CVStore(self.workspace)
        self.persona_doc: dict[str, Any] = {}
        self.application_profile_doc: dict[str, Any] = {}
        self.cv_text: str = ""
        self._cv_loaded = False
        self.apply_facts: dict[str, Any] = {}
        self._source_signature: tuple[tuple[str, int, int], ...] = ()
        self.refresh()

    def refresh(self) -> None:
        try:
            persona = self.profile_store.load_doc()
        except Exception:
            persona = {}
        self.persona_doc = persona if isinstance(persona, dict) else {}
        try:
            application_profile = self._load_application_profile()
        except Exception:
            application_profile = {}
        self.application_profile_doc = application_profile if isinstance(application_profile, dict) else {}
        # Full CV text is intentionally lazy. Most phases do not need it, and
        # both provider and external-agent paths must pay the same cost only
        # when the agent explicitly requests the full_cv resource.
        self.cv_text = ""
        self._cv_loaded = False
        self.apply_facts = self._build_apply_facts(self.persona_doc, self.application_profile_doc)
        self._source_signature = self._current_source_signature()

    def refresh_if_changed(self) -> bool:
        """Refresh metadata only when profile/CV artifacts changed on disk."""

        if self._current_source_signature() == self._source_signature:
            return False
        self.refresh()
        return True

    def release_loaded_bundles(self) -> None:
        """Drop large in-memory resource bodies after their final batch scope ends."""

        self.cv_text = ""
        self._cv_loaded = False

    def _current_source_signature(self) -> tuple[tuple[str, int, int], ...]:
        paths = [
            self.profile_store.doc_path,
            self.workspace / "profile" / "application_profile.md",
            self.cv_store.metadata_path,
        ]
        metadata = read_json(self.cv_store.metadata_path)
        active_name = str(metadata.get("active_file") or "") if isinstance(metadata, dict) else ""
        if active_name:
            paths.append(self.cv_store.current_dir / active_name)
        signature: list[tuple[str, int, int]] = []
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            signature.append((str(path), int(stat.st_mtime_ns), int(stat.st_size)))
        return tuple(signature)

    def resource_version(self, resource_id: str) -> str:
        """Return a content version for a lazily served context resource."""

        normalized = str(resource_id or "").strip().lower()
        if normalized == "apply_facts":
            payload = json.dumps(self.apply_facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            return hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if normalized == "full_persona":
            payload = json.dumps(self.persona_doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            return hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return ""

    def _load_cv_text(self) -> str:
        if self._cv_loaded:
            return self.cv_text
        try:
            cv_text = self.cv_store.load_current_text()
        except Exception:
            cv_text = ""
        self.cv_text = str(cv_text or "").strip()
        self._cv_loaded = True
        return self.cv_text

    def _has_current_cv(self) -> bool:
        try:
            return bool(self.cv_store.has_current_text())
        except Exception:
            return False

    def _load_application_profile(self) -> dict[str, Any]:
        path = self.workspace / "profile" / "application_profile.md"
        if not path.exists():
            return {}
        data, _body = parse_front_matter(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

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
    def _build_apply_facts(cls, persona_doc: dict[str, Any], application_profile_doc: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(persona_doc, dict):
            return {}
        facts: dict[str, Any] = {}
        cleaned_application_profile = cls._prune(application_profile_doc)
        if cleaned_application_profile not in ("", [], {}, None):
            facts["application_profile"] = cleaned_application_profile
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
        if self._cv_loaded and self.cv_text:
            bundles.append("full_cv")
        elif self._has_current_cv():
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
            return f"Full CV text (requested bundle `full_cv`):\n{self._load_cv_text()}"
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
            lines.append("- `apply_facts`: current lightweight structured profile facts for routine form filling, including `workspace/profile/application_profile.md` when available.")
        if "full_cv" in available:
            lines.append("- `full_cv`: current full CV text for resume-header/contact facts, education, employment history, detailed experience, or open-ended answers.")
        if "full_persona" in available:
            lines.append("- `full_persona`: current full persona profile data for detailed background and constraints.")
        lines.append("Do not request a fuller bundle unless the active page or active rule actually needs more detail.")
        return "\n".join(lines)
