"""Provider factory."""

from __future__ import annotations

from pathlib import Path

from careereng.config.schema import AppConfig, AuthConfig
from careereng.platform.observability import LLMUsageRecorder
from careereng.orchestration.agent_protocol.llm import LLMProvider, ProviderError
from .openai_provider import OpenAICompatibleProvider
from .openrouter_provider import OpenRouterProvider


def create_provider(config: AppConfig, auth: AuthConfig, *, workspace: Path | None = None) -> tuple[str, LLMProvider]:
    name = config.agent.default_provider.lower().strip()
    openai_base = config.providers.openai.api_base or "https://api.openai.com/v1"
    openrouter_base = config.providers.openrouter.api_base or "https://openrouter.ai/api/v1"
    metrics_recorder = LLMUsageRecorder(workspace) if workspace is not None else None

    if name == "openai" and auth.openai_api_key:
        return "openai", OpenAICompatibleProvider(
            api_key=auth.openai_api_key,
            api_base=openai_base,
            structured_output_mode=config.providers.openai.structured_output_mode,
            provider_name="openai",
            metrics_recorder=metrics_recorder,
        )

    if name == "openrouter" and auth.openrouter_api_key:
        return "openrouter", OpenRouterProvider(
            api_key=auth.openrouter_api_key,
            api_base=openrouter_base,
            structured_output_mode=config.providers.openrouter.structured_output_mode,
            metrics_recorder=metrics_recorder,
        )

    if auth.openrouter_api_key:
        return "openrouter", OpenRouterProvider(
            api_key=auth.openrouter_api_key,
            api_base=openrouter_base,
            structured_output_mode=config.providers.openrouter.structured_output_mode,
            metrics_recorder=metrics_recorder,
        )

    if auth.openai_api_key:
        return "openai", OpenAICompatibleProvider(
            api_key=auth.openai_api_key,
            api_base=openai_base,
            structured_output_mode=config.providers.openai.structured_output_mode,
            provider_name="openai",
            metrics_recorder=metrics_recorder,
        )

    raise ProviderError("No available provider key in auth.json")
