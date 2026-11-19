"""The activity contract, heartbeats, error classification and the claim-check codec."""

import asyncio
from collections.abc import Callable
from datetime import timedelta

import pytest
from agent_harness import FakeProvider
from agent_harness.errors import AuthenticationError, RateLimitError
from agent_harness.llm_providers import CompletionRequest
from temporalio.api.common.v1 import Payload
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from core_python.agent_runtime.activities.contract import CONTRACTS
from core_python.agent_runtime.activities.harness import HarnessActivities
from core_python.agent_runtime.activities.heartbeat import heartbeating
from core_python.agent_runtime.claim_check import ENCODING, ClaimCheckCodec, MemoryBlobStore
from core_python.agent_runtime.employees import StaticDirectory
from core_python.agent_runtime.harness.context import build_context
from core_python.agent_runtime.harness.state import AgentState, EmployeeSpec, Limits
from core_python.agent_runtime.workflows.types import ModelInput


def test_every_activity_has_a_contract() -> None:
    acts = HarnessActivities(employees=StaticDirectory({}), provider=FakeProvider())
    names = {fn.__temporal_activity_definition.name for fn in acts.all()}  # type: ignore[attr-defined]
    assert names <= set(CONTRACTS)


def test_contract_matches_the_diagram() -> None:
    model = CONTRACTS["call_model"]
    assert model.queue == "llm"
    assert model.start_to_close == timedelta(seconds=180)
    assert model.heartbeat == timedelta(seconds=15)
    assert model.retry.initial_interval == timedelta(seconds=2)
    assert model.retry.maximum_interval == timedelta(seconds=60)
    assert model.retry.maximum_attempts == 8
    assert "BudgetWouldExceed" in (model.retry.non_retryable_error_types or [])

    tool = CONTRACTS["act"]
    assert (tool.queue, tool.start_to_close, tool.heartbeat) == (
        "tools",
        timedelta(seconds=120),
        timedelta(seconds=10),
    )
    assert tool.retry.maximum_attempts == 5
    assert "UnknownOutcome" in (tool.retry.non_retryable_error_types or [])

    blueprint = CONTRACTS["load_employee"]
    assert blueprint.schedule_to_close == timedelta(minutes=5)
    assert blueprint.retry.maximum_attempts == 0  # until 5 minutes, not a count


async def test_heartbeats_while_waiting() -> None:
    env = ActivityEnvironment()
    beats: list[object] = []
    env.on_heartbeat = lambda *details: beats.append(details)

    async def slow() -> None:
        async with heartbeating(every_s=0.05):
            await asyncio.sleep(0.22)

    await env.run(slow)
    assert len(beats) >= 4


# ---- error classification inside the activity -------------------------------------------


def fails(exc: Exception) -> Callable[[CompletionRequest], Exception]:
    """A scripted reply that raises: FakeProvider raises an exception a script returns."""
    return lambda _req: exc


def directory() -> StaticDirectory:
    return StaticDirectory(
        {"sam": lambda: EmployeeSpec.build(name="Sam", instructions="", model="fake-1")}
    )


async def model_input() -> ModelInput:
    spec = await directory().get("sam")
    state = AgentState.start(run_id="r", tenant_id="t", employee_id="sam", task="hi")
    built = await build_context(spec, state)
    return ModelInput(employee_id="sam", limits=Limits(), request=built.request, state=state)


async def test_bad_key_is_reported_as_not_retryable() -> None:
    acts = HarnessActivities(
        employees=directory(),
        provider=FakeProvider([fails(AuthenticationError("bad key", provider="x"))]),
    )
    with pytest.raises(ApplicationError) as caught:
        await ActivityEnvironment().run(acts.call_model, await model_input())
    assert caught.value.non_retryable
    assert caught.value.type == "AuthenticationError"


async def test_rate_limit_stays_retryable() -> None:
    acts = HarnessActivities(
        employees=directory(),
        provider=FakeProvider([fails(RateLimitError("slow down", provider="x"))]),
    )
    with pytest.raises(RateLimitError):
        await ActivityEnvironment().run(acts.call_model, await model_input())


async def test_unknown_employee_is_not_retryable() -> None:
    acts = HarnessActivities(employees=StaticDirectory({}), provider=FakeProvider())
    with pytest.raises(ApplicationError) as caught:
        await ActivityEnvironment().run(acts.load_employee, "nobody")
    assert caught.value.non_retryable and caught.value.type == "EmployeeNotFound"


async def test_raw_provider_payload_is_dropped() -> None:
    acts = HarnessActivities(employees=directory(), provider=FakeProvider(["hello"]))
    resp = await ActivityEnvironment().run(acts.call_model, await model_input())
    assert resp.text == "hello" and resp.raw is None


# ---- claim check --------------------------------------------------------------------------


async def test_small_payloads_pass_through_untouched() -> None:
    codec = ClaimCheckCodec(MemoryBlobStore(), threshold=1024)
    small = Payload(metadata={"encoding": b"json/plain"}, data=b'{"a": 1}')
    assert await codec.encode([small]) == [small]


async def test_large_payloads_are_stored_and_restored() -> None:
    store = MemoryBlobStore()
    codec = ClaimCheckCodec(store, threshold=1024)
    big = Payload(metadata={"encoding": b"json/plain"}, data=b"x" * 50_000)

    (encoded,) = await codec.encode([big])
    assert encoded.metadata["encoding"] == ENCODING
    assert encoded.ByteSize() < 200, "only a reference goes into history"
    assert len(store.blobs) == 1

    (decoded,) = await codec.decode([encoded])
    assert decoded == big


async def test_same_payload_is_stored_once() -> None:
    store = MemoryBlobStore()
    codec = ClaimCheckCodec(store, threshold=10)
    big = Payload(data=b"y" * 100)
    first = await codec.encode([big])
    second = await codec.encode([big])
    assert first == second and len(store.blobs) == 1
