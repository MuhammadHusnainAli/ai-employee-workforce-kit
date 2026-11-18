"""The extractor port and its Model Gateway adapter.

`GatewayExtractor` asks the model for structured output (JSON matching `Extraction`) through
agent-harness-adk's provider, pointed at the Model Gateway. `BaselineExtractor` uses no model:
the episode only. Either way the result is a list of candidates for the governance filter.

Idempotency: the extractor is a pure function of the source as far as the brain is concerned.
It runs inside a Temporal activity, whose recorded result is reused on replay, and the commit
derives memory ids from the source event id, so a second extraction of the same event stores
nothing new.
"""

from __future__ import annotations

import logging
from typing import Protocol

from agent_harness.llm_providers import CompletionRequest, Provider
from agent_harness.types import Message

from brain_service.domain.write import extract
from brain_service.domain.write.model import Candidate, Source

log = logging.getLogger(__name__)


class Extractor(Protocol):
    async def extract(self, source: Source) -> list[Candidate]: ...


class BaselineExtractor:
    """No model: remember the episode itself."""

    async def extract(self, source: Source) -> list[Candidate]:
        return extract.candidates(source, None)


class GatewayExtractor:
    def __init__(self, provider: Provider, model: str, *, max_tokens: int = 2000) -> None:
        self.provider = provider
        self.model = model
        self.max_tokens = max_tokens

    async def extract(self, source: Source) -> list[Candidate]:
        """The episode plus what the model found. A model error raises (the activity retries);
        output that does not validate is dropped and the episode alone is kept."""
        req = CompletionRequest.model_validate(
            {
                "model": self.model,
                "system": extract.SYSTEM_PROMPT,
                "messages": [Message.user(extract.prompt(source))],
                "response_schema": extract.Extraction.model_json_schema(),
                "max_tokens": self.max_tokens,
                "temperature": 0.0,
                "metadata": {"purpose": "brain-extract", "source_event": str(source.event_id)},
            }
        )
        resp = await self.provider.complete(req)
        return extract.candidates(source, resp.message.text)


def gateway_provider(base_url: str, api_key: str) -> Provider:
    from agent_harness.llm_providers import OpenAICompatibleProvider

    # No retries here: the ingest activity's retry policy is the one place calls are retried.
    return OpenAICompatibleProvider(api_key=api_key, base_url=base_url, max_retries=0, timeout=60.0)
