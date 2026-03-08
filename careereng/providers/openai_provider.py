"""OpenAI-compatible provider client."""

from __future__ import annotations

import json
from typing import Any

import httpx

from careereng.providers.base import LLMProvider, ProviderError, StructuredOutputResult


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, *, api_key: str, api_base: str, structured_output_mode: str = "auto"):
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.structured_output_mode = str(structured_output_mode or "auto").strip().lower() or "auto"
        self._unsupported_structured_modes: set[str] = set()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise ProviderError("API key is missing")
        try:
            resp = httpx.post(
                f"{self.api_base}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=60,
            )
        except Exception as exc:
            raise ProviderError(f"provider request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise ProviderError(f"provider error {resp.status_code}: {resp.text[:300]}")

        try:
            data = resp.json()
        except Exception as exc:
            raise ProviderError("invalid provider response") from exc
        if not isinstance(data, dict):
            raise ProviderError("invalid provider response")
        return data

    def _extract_text_content(self, data: dict[str, Any]) -> str:
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise ProviderError("invalid provider response") from exc
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    if isinstance(item.get("text"), str):
                        parts.append(item["text"])
                    elif isinstance(item.get("content"), str):
                        parts.append(item["content"])
                elif item is not None:
                    parts.append(str(item))
            return "".join(parts)
        if isinstance(content, (dict, list)):
            return json.dumps(content, ensure_ascii=False)
        return str(content or "")

    @staticmethod
    def _safe_schema_name(value: str) -> str:
        raw = "".join(ch if ch.isalnum() else "_" for ch in str(value or "response"))
        raw = raw.strip("_") or "response"
        if raw[0].isdigit():
            raw = "schema_" + raw
        return raw[:64]

    def _structured_mode_candidates(self, requested: str) -> list[str]:
        mode = str(requested or self.structured_output_mode or "auto").strip().lower() or "auto"
        if mode in {"text_repair", "text_repair_only", "plain_text"}:
            return ["text_repair"]
        if mode == "json_object":
            return ["json_object", "text_repair"]
        if mode == "json_schema":
            return ["json_schema", "json_object", "text_repair"]
        return ["json_schema", "json_object", "text_repair"]

    @staticmethod
    def _should_cache_structured_failure(error_text: str) -> bool:
        lowered = str(error_text or "").lower()
        if "provider error 400" not in lowered and "provider error 422" not in lowered:
            return False
        markers = (
            "response_format",
            "json_schema",
            "json_object",
            "unsupported",
            "not supported",
            "unknown parameter",
            "extra inputs are not permitted",
            "invalid type",
        )
        return any(marker in lowered for marker in markers)

    def _structured_payload(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        schema: dict[str, Any],
        schema_name: str,
        mode: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if mode == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": self._safe_schema_name(schema_name),
                    "schema": schema,
                    "strict": True,
                },
            }
        elif mode == "json_object":
            payload["response_format"] = {"type": "json_object"}
        return payload

    def chat(self, messages: list[dict[str, Any]], *, model: str) -> str:
        payload = {
            "model": model,
            "messages": messages,
        }
        return self._extract_text_content(self._post_chat(payload))

    def chat_json(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        schema: dict[str, Any] | None = None,
        schema_name: str = "response",
        json_mode: str = "auto",
    ) -> StructuredOutputResult:
        requested_mode = str(json_mode or self.structured_output_mode or "auto").strip().lower() or "auto"
        if not isinstance(schema, dict) or not schema:
            return super().chat_json(
                messages,
                model=model,
                schema=schema,
                schema_name=schema_name,
                json_mode=requested_mode,
            )

        for mode in self._structured_mode_candidates(requested_mode):
            if mode == "text_repair":
                break
            if requested_mode == "auto" and mode in self._unsupported_structured_modes:
                continue
            try:
                payload = self._structured_payload(
                    messages=messages,
                    model=model,
                    schema=schema,
                    schema_name=schema_name,
                    mode=mode,
                )
                raw = self._extract_text_content(self._post_chat(payload))
            except ProviderError as exc:
                if requested_mode == "auto" and self._should_cache_structured_failure(str(exc)):
                    self._unsupported_structured_modes.add(mode)
                continue
            parsed = self.parse_json_object(raw)
            if isinstance(parsed, dict):
                return StructuredOutputResult(
                    data=parsed,
                    raw=raw,
                    mode=mode,
                    used_fallback=(requested_mode == "auto" and mode != "json_schema"),
                )

        result = super().chat_json(
            messages,
            model=model,
            schema=schema,
            schema_name=schema_name,
            json_mode=requested_mode,
        )
        result.used_fallback = True
        return result
