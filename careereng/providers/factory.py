"""Provider factory."""

from __future__ import annotations

from careereng.config.schema import AppConfig, AuthConfig
from careereng.providers.base import LLMProvider, ProviderError
from careereng.providers.openai_provider import OpenAICompatibleProvider
from careereng.providers.openrouter_provider import OpenRouterProvider


def create_provider(config: AppConfig, auth: AuthConfig) -> tuple[str, LLMProvider]:
    name = config.agent.default_provider.lower().strip()
    openai_base = config.providers.openai.api_base or "https://api.openai.com/v1"
    openrouter_base = config.providers.openrouter.api_base or "https://openrouter.ai/api/v1"

    if name == "openai" and auth.openai_api_key:
        return "openai", OpenAICompatibleProvider(
            api_key=auth.openai_api_key,
            api_base=openai_base,
            structured_output_mode=config.providers.openai.structured_output_mode,
        )

    if name == "openrouter" and auth.openrouter_api_key:
        return "openrouter", OpenRouterProvider(
            api_key=auth.openrouter_api_key,
            api_base=openrouter_base,
            structured_output_mode=config.providers.openrouter.structured_output_mode,
        )

    if auth.openrouter_api_key:
        return "openrouter", OpenRouterProvider(
            api_key=auth.openrouter_api_key,
            api_base=openrouter_base,
            structured_output_mode=config.providers.openrouter.structured_output_mode,
        )

    if auth.openai_api_key:
        return "openai", OpenAICompatibleProvider(
            api_key=auth.openai_api_key,
            api_base=openai_base,
            structured_output_mode=config.providers.openai.structured_output_mode,
        )

    raise ProviderError("No available provider key in auth.json")
