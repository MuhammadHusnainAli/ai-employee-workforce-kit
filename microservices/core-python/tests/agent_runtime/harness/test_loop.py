"""The Phase 1 exit test, and end-to-end runs of the loop on a scripted model."""

from typing import Any

from agent_harness import FakeProvider, tool_call
from agent_harness.llm_providers import CompletionRequest
from agent_harness.tools import Tool, ToolContext
from agent_harness.types import ToolUseBlock

from core_python.agent_runtime.harness.loop import resume_approved, run, step
from core_python.agent_runtime.harness.state import AgentState, EmployeeSpec, Limits, Tier


class World:
    """Tools with side effects we can count: every call records its idempotency key."""

    def __init__(self) -> None:
        self.keys: list[str] = []
        self.sent: list[str] = []

    def tools(self) -> list[Tool]:
        world = self

        def lookup(order: int, ctx: ToolContext) -> str:
            world.keys.append(ctx.state["idempotency_key"])
            return f"order {order}: shipped Tuesday"

        def send_email(to: str, body: str, ctx: ToolContext) -> str:
            world.keys.append(ctx.state["idempotency_key"])
            world.sent.append(to)
            return "sent"

        lookup.__doc__ = "Look an order up.\n\nArgs:\n    order: the order number."
        send_email.__doc__ = "Send an email.\n\nArgs:\n    to: recipient.\n    body: text."
        return [Tool(lookup), Tool(send_email)]


def call(call_id: str, name: str, **args: Any) -> ToolUseBlock:
    """A scripted tool call with a fixed id (a real model's ids are recorded in its reply)."""
    return ToolUseBlock(id=call_id, name=name, input=args)


def script() -> list[Any]:
    """A run with a bad call (repaired), a read, an approval-gated write and a final answer."""
    return [
        call("c1", "lookup", order="forty-two"),  # invalid: repaired
        call("c2", "lookup", order=42),
        call("c3", "send_email", to="ana@example.com", body="Ships Tuesday"),  # needs approval
        "Order 42 ships Tuesday. I emailed Ana.",
    ]


def employee(world: World) -> EmployeeSpec:
    tiers: dict[str, Tier] = {"lookup": "R0", "send_email": "R2"}
    return EmployeeSpec.build(
        name="Sam from Support",
        instructions="Answer order questions. Look the order up first.",
        model="fake-1",
        tools=world.tools(),
        tiers=tiers,
        limits=Limits(max_steps=10),
    )


def fresh() -> AgentState:
    return AgentState.start(
        run_id="run-1", tenant_id="t1", employee_id="e1", task="Where is order 42? Tell Ana."
    )


def without_ts(value: Any) -> Any:
    """Timestamps come from the clock at call time; everything else must match exactly."""
    if isinstance(value, dict):
        return {k: without_ts(v) for k, v in value.items() if k != "ts"}
    if isinstance(value, list):
        return [without_ts(v) for v in value]
    return value


def prompts(requests: list[CompletionRequest]) -> list[Any]:
    return [without_ts(r.model_dump(mode="json")) for r in requests]


async def test_run_until_approval_then_finish() -> None:
    world = World()
    spec, provider = employee(world), FakeProvider(script())

    waiting = await run(spec, provider, fresh())
    assert (waiting.status, waiting.stop_reason) == ("waiting_human", "approval")
    assert world.sent == [], "nothing is sent before a person approves"

    resumed = await resume_approved(spec, waiting)
    done = await run(spec, provider, resumed)

    assert (done.status, done.stop_reason) == ("completed", "final")
    assert done.output == "Order 42 ships Tuesday. I emailed Ana."
    assert world.sent == ["ana@example.com"]
    assert world.keys == ["run-1:1:0", "run-1:2:0"]  # one key per call, never reused


async def test_exit_stepping_from_serialised_state_matches_an_uninterrupted_run() -> None:
    """Phase 1 exit: drive the employee one step at a time, rebuilding AgentState from JSON
    before every step (as a new worker would after a crash), and get the same result as an
    uninterrupted run: the same prompts, each tool called once with the same key, the same
    final state."""
    # A: uninterrupted.
    world_a, provider_a = World(), FakeProvider(script())
    spec_a = employee(world_a)
    a = await run(spec_a, provider_a, fresh())
    a = await run(spec_a, provider_a, await resume_approved(spec_a, a))

    # B: one step at a time, every state written out and read back in between.
    world_b, provider_b = World(), FakeProvider(script())
    stored = fresh().to_json()
    steps = 0
    while True:
        spec_b = employee(world_b)  # rebuilt each step, like a fresh worker
        b = AgentState.from_json(stored)
        if b.status == "waiting_human":
            b = await resume_approved(spec_b, b)
        elif b.status == "running":
            b, _ = await step(spec_b, provider_b, b)
            steps += 1
        else:
            break
        stored = b.to_json()

    b = AgentState.from_json(stored)
    assert steps == 4
    assert prompts(provider_b.requests) == prompts(provider_a.requests)
    assert world_b.keys == world_a.keys == ["run-1:1:0", "run-1:2:0"]
    assert world_b.sent == world_a.sent == ["ana@example.com"]
    assert without_ts(b.model_dump(mode="json")) == without_ts(a.model_dump(mode="json"))
    assert (b.status, b.output) == ("completed", "Order 42 ships Tuesday. I emailed Ana.")


async def test_budget_stop_happens_before_any_spend() -> None:
    from agent_harness.llm_providers.base import ModelInfo, register_model

    register_model(
        ModelInfo(id="pricey-loop-model", provider="fake", input_cost=50, output_cost=50)
    )
    world = World()
    spec = employee(world)
    spec.model = "pricey-loop-model"
    spec.limits = Limits(max_cost_usd=0.01)
    provider = FakeProvider(script())

    stopped = await run(spec, provider, fresh())

    assert (stopped.status, stopped.stop_reason) == ("waiting_human", "budget")
    assert provider.requests == [] and world.keys == []


async def test_runaway_loop_is_stopped() -> None:
    world = World()
    spec = employee(world)
    provider = FakeProvider([tool_call("lookup", order=42)], loop=True)

    stopped = await run(spec, provider, fresh())

    assert (stopped.status, stopped.stop_reason) == ("waiting_human", "loop")
    assert len(world.keys) == 2  # the third identical call is not run


async def test_step_refuses_a_finished_run() -> None:
    world = World()
    spec = employee(world)
    done = await run(spec, FakeProvider(["Done."]), fresh())
    assert done.status == "completed"
    try:
        await step(spec, FakeProvider(), done)
    except ValueError as exc:
        assert "completed" in str(exc)
    else:
        raise AssertionError("stepping a completed run must fail")
