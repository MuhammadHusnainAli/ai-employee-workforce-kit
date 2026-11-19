import pytest
from agent_harness import FakeProvider
from agent_harness.errors import (
    AuthenticationError,
    ContextWindowExceededError,
    ProviderTimeoutError,
    QuotaExceededError,
    RateLimitError,
)
from agent_harness.llm_providers.base import ModelInfo, register_model
from agent_harness.types import Usage

from core_python.agent_runtime.harness.context import build_context
from core_python.agent_runtime.harness.model import (
    BudgetWouldExceed,
    call_model,
    classify,
    worst_case_cost,
)
from core_python.agent_runtime.harness.state import AgentState, EmployeeSpec, Limits

# A priced model so the budget check has something to count: $10 / $30 per 1M tokens.
register_model(
    ModelInfo(id="priced-test-model", provider="fake", input_cost=10.0, output_cost=30.0)
)


def spec(model: str = "fake-1", limits: Limits | None = None) -> EmployeeSpec:
    return EmployeeSpec.build(
        name="Sam", instructions="Be brief.", model=model, limits=limits or Limits()
    )


def state() -> AgentState:
    return AgentState.start(run_id="r", tenant_id="t", employee_id="e", task="Hello")


async def test_calls_the_provider_once_with_the_built_request() -> None:
    provider = FakeProvider(["Hi there."])
    s = spec()
    built = await build_context(s, state())
    resp = await call_model(provider, built.request, state(), s.limits)
    assert resp.text == "Hi there."
    assert len(provider.requests) == 1
    assert provider.requests[0].system == built.request.system


async def test_worst_case_counts_the_full_output_allowance() -> None:
    s = spec("priced-test-model", Limits(max_output_tokens=1000))
    built = await build_context(s, state())
    # at least 1000 output tokens at $30 per 1M
    assert worst_case_cost(built.request) >= 1000 * 30 / 1_000_000


async def test_stops_before_overspending_and_never_calls_the_model() -> None:
    provider = FakeProvider(["should not be reached"])
    s = spec("priced-test-model", Limits(max_cost_usd=0.05, max_output_tokens=4000))
    st = state()
    st.usage = Usage(cost_usd=0.04)  # $0.01 left; worst case is about $0.12
    built = await build_context(s, st)
    with pytest.raises(BudgetWouldExceed) as caught:
        await call_model(provider, built.request, st, s.limits)
    assert caught.value.remaining_usd == pytest.approx(0.01)
    assert provider.requests == []


async def test_unpriced_model_is_never_blocked_by_budget() -> None:
    provider = FakeProvider(["ok"])
    s = spec("fake-1", Limits(max_cost_usd=0.000001))
    built = await build_context(s, state())
    assert (await call_model(provider, built.request, state(), s.limits)).text == "ok"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (RateLimitError("slow down", provider="x"), "retryable"),
        (ProviderTimeoutError("timeout", provider="x"), "retryable"),
        (QuotaExceededError("quota", provider="x"), "non_retryable"),
        (AuthenticationError("bad key", provider="x"), "non_retryable"),
        (ContextWindowExceededError("too long", provider="x"), "non_retryable"),
        (BudgetWouldExceed(1.0, 0.0), "non_retryable"),
        (RuntimeError("bug"), "non_retryable"),
    ],
)
def test_classify(exc: BaseException, expected: str) -> None:
    assert classify(exc) == expected
