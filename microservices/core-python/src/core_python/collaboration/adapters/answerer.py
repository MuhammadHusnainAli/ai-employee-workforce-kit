"""A colleague answering on a call: one model call through the Model Gateway, no tools."""

from __future__ import annotations

from agent_harness.llm_providers import CompletionRequest, Provider
from agent_harness.types import Message

from core_python.collaboration.domain.conversations import (
    MAX_ANSWER_WORDS,
    Colleague,
    call_prompt,
    trim_words,
)


class Answerer:
    def __init__(self, provider: Provider, *, max_tokens: int = 400) -> None:
        self.provider = provider
        self.max_tokens = max_tokens

    async def answer(
        self, callee: Colleague, caller: str, transcript: list[tuple[str, str]], question: str
    ) -> str:
        system, turns = call_prompt(callee, caller, transcript, question)
        messages = [
            Message.user(t) if role == "user" else Message.assistant(t) for role, t in turns
        ]
        req = CompletionRequest.model_validate(
            {
                "model": callee.model,
                "system": system,
                "messages": messages,
                "max_tokens": self.max_tokens,
                "temperature": 0.3,
                "metadata": {"purpose": "call", "employee_id": callee.employee_id},
            }
        )
        resp = await self.provider.complete(req)
        return trim_words(resp.message.text or "", MAX_ANSWER_WORDS) or "(no answer)"


def gateway_provider(base_url: str, api_key: str) -> Provider:
    from agent_harness.llm_providers import OpenAICompatibleProvider

    return OpenAICompatibleProvider(api_key=api_key, base_url=base_url, max_retries=1, timeout=60.0)
