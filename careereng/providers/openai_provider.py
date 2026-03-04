"""OpenAI-compatible provider client."""

from __future__ import annotations

from typing import Any

import httpx

from careereng.providers.base import LLMProvider, ProviderError


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, *, api_key: str, api_base: str):
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")

    def chat(self, messages: list[dict[str, Any]], *, model: str) -> str:
        if not self.api_key:
            raise ProviderError("API key is missing")

        payload = {
            "model": model,
            "messages": messages,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = httpx.post(
                f"{self.api_base}/chat/completions",
                json=payload,
                headers=headers,
                timeout=60,
            )
        except Exception as exc:
            raise ProviderError(f"provider request failed: {exc}") from exc

        if resp.status_code >= 400:
            raise ProviderError(f"provider error {resp.status_code}: {resp.text[:300]}")

        try:
            data: dict[str, Any] = resp.json()
            return str(data["choices"][0]["message"]["content"] or "")
        except Exception as exc:
            raise ProviderError("invalid provider response") from exc
