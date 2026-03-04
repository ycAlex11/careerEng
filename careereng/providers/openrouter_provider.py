"""OpenRouter provider."""

from __future__ import annotations

from careereng.providers.openai_provider import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    def __init__(self, *, api_key: str, api_base: str = "https://openrouter.ai/api/v1"):
        super().__init__(api_key=api_key, api_base=api_base)
