"""Patch candidate extractor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from careereng.providers.base import LLMProvider
from careereng.storage.intent_store import DEFAULT_INTENT
from careereng.storage.profile_store import DEFAULT_PERSONA
from careereng.utils import ensure_dir, now_iso


class CandidateExtractor:
    SYSTEM_MANAGED_KEYS = {"version", "updated_at"}
    MAX_SOURCE_CHARS_FOR_REPAIR = 12000

    def __init__(
        self,
        evals_dir: Path | None = None,
        *,
        skills_dir: Path | None = None,
        debug_dir: Path | None = None,
    ):
        if evals_dir is None:
            evals_dir = skills_dir
        if evals_dir is None:
            evals_dir = Path(".")
        self.skills_dir = evals_dir
        self.debug_dir = debug_dir
        self.profile_few_shot = self._load_yaml(evals_dir / "profile_extractor" / "few_shot.yaml")
        self.intent_few_shot = self._load_yaml(evals_dir / "intent_extractor" / "few_shot.yaml")

    def _load_yaml(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"version": "v1", "examples": []}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": "v1", "examples": []}
        return data if isinstance(data, dict) else {"version": "v1", "examples": []}

    def _parse_json(self, text: str) -> dict[str, Any] | None:
        raw = text.strip()
        candidates = [raw]
        if raw.startswith("```"):
            start = raw.find("\n")
            end = raw.rfind("```")
            if start != -1 and end > start:
                candidates.append(raw[start + 1 : end].strip())
        first, last = raw.find("{"), raw.rfind("}")
        if first != -1 and last > first:
            candidates.append(raw[first : last + 1].strip())
        for candidate in candidates:
            try:
                data = json.loads(candidate)
            except Exception:
                continue
            if isinstance(data, dict):
                return data
        return None

    def _filter_patch(self, patch: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in patch.items():
            if key not in schema:
                continue
            schema_val = schema[key]
            if isinstance(value, dict) and isinstance(schema_val, dict):
                nested = self._filter_patch(value, schema_val)
                if nested:
                    out[key] = nested
            elif isinstance(schema_val, list):
                if isinstance(value, list):
                    out[key] = value
            elif isinstance(schema_val, str):
                if isinstance(value, str):
                    out[key] = value
            elif isinstance(schema_val, bool):
                if isinstance(value, bool):
                    out[key] = value
            elif isinstance(schema_val, int) and not isinstance(schema_val, bool):
                if isinstance(value, int) and not isinstance(value, bool):
                    out[key] = value
            elif isinstance(schema_val, float):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    out[key] = float(value)
            else:
                out[key] = value
        return out

    def _parse_filter_and_sanitize(self, text: str, schema: dict[str, Any]) -> dict[str, Any]:
        parsed = self._parse_json(text)
        if not isinstance(parsed, dict) or not parsed:
            return {}
        filtered = self._filter_patch(parsed, schema)
        for key in self.SYSTEM_MANAGED_KEYS:
            filtered.pop(key, None)
        return filtered

    def _repair_patch(
        self,
        provider: LLMProvider,
        model: str,
        *,
        raw_output: str,
        schema: dict[str, Any],
        skill_text: str = "",
        source_payload: str = "",
    ) -> dict[str, Any]:
        repair_prompt = (
            "Normalize the previous model output into a strict JSON patch object.\n"
            "Return JSON only.\n"
            "Only include keys that exist in the provided schema.\n"
            "Do not include version or updated_at.\n"
            "Your response must start with '{' and end with '}'.\n"
            "If there is no supported evidence, return {}."
        )
        messages = [{"role": "system", "content": repair_prompt}]
        if skill_text.strip():
            messages.append(
                {
                    "role": "system",
                    "content": "Follow this resume parsing skill policy:\n" + skill_text.strip(),
                }
            )
        repair_input: dict[str, Any] = {
            "schema": schema,
            "previous_output": raw_output,
        }
        if source_payload.strip():
            repair_input["source_text"] = source_payload[: self.MAX_SOURCE_CHARS_FOR_REPAIR]
        messages.append(
            {
                "role": "user",
                "content": json.dumps(repair_input, ensure_ascii=False),
            }
        )
        try:
            repaired = provider.chat(messages, model=model)
        except Exception:
            return {}
        return self._parse_filter_and_sanitize(repaired, schema)

    def _debug_resume_extract(self, label: str, payload: dict[str, Any]) -> None:
        if not self.debug_dir:
            return
        try:
            ensure_dir(self.debug_dir)
            path = self.debug_dir / f"{now_iso()[:10]}.jsonl"
            row = {"ts": now_iso(), "label": label, **payload}
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            return

    def _extract_patch(
        self,
        provider: LLMProvider,
        model: str,
        *,
        prompt: str,
        examples: list[dict[str, Any]],
        payload: str,
        schema: dict[str, Any],
        skill_text: str = "",
        use_few_shot: bool = True,
        debug_label: str = "",
    ) -> dict[str, Any]:
        messages = [{"role": "system", "content": prompt}]
        if skill_text.strip():
            messages.append(
                {
                    "role": "system",
                    "content": "Follow this resume parsing skill policy:\n" + skill_text.strip(),
                }
            )
        if use_few_shot and examples:
            messages.append({"role": "system", "content": json.dumps(examples[:10], ensure_ascii=False)})
        messages.append({"role": "user", "content": payload})
        try:
            out = provider.chat(messages, model=model)
        except Exception:
            return {}
        patch = self._parse_filter_and_sanitize(out, schema)
        debug_payload: dict[str, Any] = {
            "prompt": prompt,
            "first_output_preview": out[:1200],
            "first_patch": patch,
        }
        if patch:
            if debug_label:
                debug_payload["repair_patch"] = {}
                self._debug_resume_extract(debug_label, debug_payload)
            return patch
        if not out.strip():
            if debug_label:
                debug_payload["repair_patch"] = {}
                self._debug_resume_extract(debug_label, debug_payload)
            return {}
        repaired = self._repair_patch(
            provider,
            model,
            raw_output=out,
            schema=schema,
            skill_text=skill_text,
            source_payload=payload,
        )
        if debug_label:
            debug_payload["repair_patch"] = repaired
            self._debug_resume_extract(debug_label, debug_payload)
        return repaired

    def extract_profile_patch(
        self,
        provider: LLMProvider,
        model: str,
        text: str,
        *,
        skill_text: str = "",
        use_few_shot: bool = True,
        debug_label: str = "",
    ) -> dict[str, Any]:
        return self._extract_patch(
            provider,
            model,
            prompt=(
                "Extract persona patch JSON from text. Only use existing persona schema keys. "
                "Return JSON object only."
            ),
            examples=self.profile_few_shot.get("examples", []),
            payload=text,
            schema=DEFAULT_PERSONA,
            skill_text=skill_text,
            use_few_shot=use_few_shot,
            debug_label=debug_label,
        )

    def extract_intent_patch(self, provider: LLMProvider, model: str, text: str) -> dict[str, Any]:
        return self._extract_patch(
            provider,
            model,
            prompt="Extract intent patch JSON. Return JSON only.",
            examples=self.intent_few_shot.get("examples", []),
            payload=text,
            schema=DEFAULT_INTENT,
        )

    def extract_resume_intent_patch(
        self,
        provider: LLMProvider,
        model: str,
        *,
        resume_text: str,
        persona: dict[str, Any],
        skill_text: str = "",
        use_few_shot: bool = False,
        debug_label: str = "",
    ) -> dict[str, Any]:
        return self._extract_patch(
            provider,
            model,
            prompt=(
                "Infer a conservative intent patch JSON from resume + persona. "
                "Only include fields with clear evidence. "
                "Do not fabricate constraints. Return JSON object only."
            ),
            examples=self.intent_few_shot.get("examples", []),
            payload=json.dumps({"resume": resume_text, "persona": persona}, ensure_ascii=False),
            schema=DEFAULT_INTENT,
            skill_text=skill_text,
            use_few_shot=use_few_shot,
            debug_label=debug_label,
        )
