"""Provider abstractions."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class ProviderError(RuntimeError):
    """Raised when provider request fails."""


@dataclass
class StructuredOutputResult:
    data: dict[str, Any]
    raw: str = ""
    repaired_raw: str = ""
    mode: str = "text_repair"
    used_fallback: bool = False


class LLMProvider(ABC):
    @abstractmethod
    def chat(self, messages: list[dict[str, Any]], *, model: str) -> str:
        """Run one chat completion call and return text."""

    @staticmethod
    def parse_json_object(text: str) -> dict[str, Any] | None:
        raw = (text or "").strip()
        if not raw:
            return None
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

    def chat_json(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        schema: dict[str, Any] | None = None,
        schema_name: str = "response",
        json_mode: str = "auto",
    ) -> StructuredOutputResult:
        raw = self.chat(messages, model=model)
        parsed = self.parse_json_object(raw)
        if isinstance(parsed, dict):
            return StructuredOutputResult(data=parsed, raw=raw, mode="plain_text")

        repair_prompt = "Convert the previous output into a strict JSON object only. Do not add markdown or commentary."
        repair_input: dict[str, Any] = {
            "schema_name": schema_name,
            "previous_output": raw,
        }
        if isinstance(schema, dict) and schema:
            repair_prompt += " Match the requested schema as closely as possible. Omit unknown fields."
            repair_input["schema"] = schema
        repair_messages = [
            {"role": "system", "content": repair_prompt},
            {"role": "user", "content": json.dumps(repair_input, ensure_ascii=False)},
        ]
        repaired_raw = self.chat(repair_messages, model=model)
        parsed = self.parse_json_object(repaired_raw)
        return StructuredOutputResult(
            data=parsed if isinstance(parsed, dict) else {},
            raw=raw,
            repaired_raw=repaired_raw,
            mode="text_repair",
            used_fallback=True,
        )
