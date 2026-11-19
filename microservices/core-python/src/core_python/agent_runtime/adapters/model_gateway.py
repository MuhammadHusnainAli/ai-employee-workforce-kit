"""The Model Gateway client: agent-harness-adk's OpenAICompatibleProvider pointed at LiteLLM.

The gateway owns provider choice, fallbacks, budgets and keys. This side keeps no retries of
its own (max_retries=0): the call_model activity's retry policy is the one place a failed
call is retried, so attempts never multiply.
"""

from __future__ import annotations

from agent_harness.llm_providers import OpenAICompatibleProvider

from core_python.agent_runtime.settings import Settings


def gateway_provider(settings: Settings) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        api_key=settings.model_gateway_key,
        base_url=settings.model_gateway_url,
        max_retries=0,
        timeout=170.0,  # inside call_model's 180 s start-to-close timeout
    )
