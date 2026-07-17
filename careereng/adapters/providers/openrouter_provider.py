"""OpenRouter provider."""

from __future__ import annotations

from careereng.platform.observability import LLMUsageRecorder
from .openai_provider import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    def __init__(
        self,
        *,
        api_key: str,
        api_base: str = "https://openrouter.ai/api/v1",
        structured_output_mode: str = "auto",
        metrics_recorder: LLMUsageRecorder | None = None,
    ):
        super().__init__(
            api_key=api_key,
            api_base=api_base,
            structured_output_mode=structured_output_mode,
            provider_name="openrouter",
            metrics_recorder=metrics_recorder,
        )
