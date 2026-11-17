"""Stage 2: one model call, through an agent-harness-adk Provider (agent-harness.md).

In the platform the provider is OpenAICompatibleProvider pointed at the Model Gateway
(LiteLLM), the only path to any LLM. Tests use FakeProvider.

Before the call, the worst case (the whole prompt plus the full output allowance) is priced.
If that could push the run past its budget, the call is not made: the run stops and asks a
person instead of overspending.
"""

from __future__ import annotations

from typing import Literal

from agent_harness.context import conversation_tokens, estimate_tokens
from agent_harness.errors import (
    AuthenticationError,
    ContextWindowExceededError,
    InvalidRequestError,
    ProviderConnectionError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    QuotaExceededError,
    RateLimitError,
)
from agent_harness.llm_providers import CompletionRequest, Provider
from agent_harness.llm_providers.base import estimate_cost
from agent_harness.types import ModelResponse, Usage

from agent_runtime.harness.state import AgentState, Limits


class BudgetWouldExceed(Exception):
    """The next call could cost more than the run has left. Raised before spending."""

    def __init__(self, estimate_usd: float, remaining_usd: float) -> None:
        super().__init__(
            f"the next model call could cost ${estimate_usd:.4f}, "
            f"the run has ${remaining_usd:.4f} left"
        )
        self.estimate_usd = estimate_usd
        self.remaining_usd = remaining_usd


def worst_case_cost(request: CompletionRequest) -> float:
    """Price of the prompt plus the full output allowance, at the model's list price."""
    prompt = conversation_tokens(request.messages) + estimate_tokens(request.system or "")
    prompt += sum(
        estimate_tokens(str(t.parameters)) + estimate_tokens(t.description) for t in request.tools
    )
    usage = Usage(input_tokens=prompt, output_tokens=request.max_tokens)
    return estimate_cost(request.model, usage)


def check_budget(request: CompletionRequest, state: AgentState, limits: Limits) -> None:
    remaining = limits.max_cost_usd - state.usage.cost_usd
    estimate = worst_case_cost(request)
    if estimate > remaining:
        raise BudgetWouldExceed(estimate, max(remaining, 0.0))


async def call_model(
    provider: Provider, request: CompletionRequest, state: AgentState, limits: Limits
) -> ModelResponse:
    """Check the budget, then call the model once. Provider errors propagate; see classify()."""
    check_budget(request, state, limits)
    return await provider.complete(request)


ErrorClass = Literal["retryable", "non_retryable"]


def classify(exc: BaseException) -> ErrorClass:
    """How the Temporal activity should treat a failed model call (activity contract).

    Rate limits, timeouts and outages are retried with backoff (and the gateway falls back
    across providers). A bad request, bad credentials, an exhausted quota or an overflowing
    context fail the same way every time, so they are not retried.
    """
    if isinstance(exc, QuotaExceededError | AuthenticationError | InvalidRequestError):
        return "non_retryable"
    if isinstance(exc, ContextWindowExceededError | BudgetWouldExceed):
        return "non_retryable"
    if isinstance(
        exc,
        RateLimitError | ProviderTimeoutError | ProviderConnectionError | ProviderUnavailableError,
    ):
        return "retryable"
    return "non_retryable"
