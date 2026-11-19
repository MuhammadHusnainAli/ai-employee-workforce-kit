"""Stage 4: act, the tool router (agent-harness.md).

Runs the calls `decide` approved, through agent-harness-adk's Tool.run (validates arguments,
never raises, returns a ToolOutcome).

- Order: calls run in the order the model gave. Consecutive read-only (R0) calls run in
  parallel, at most `max_parallel_reads` at a time. Every other call runs alone, after
  everything before it has finished, so a write never races a read it depends on.
- Idempotency: each call gets the key `run_id:step:call_idx` in `ToolContext.state`. Tools
  with side effects pass it to the system they write to, so a retried step does not repeat
  the effect.
- Untrusted output: every result is wrapped as `<data source=... trust=untrusted>`, so the
  model treats it as data, never as instructions.

In the platform, tools are MCP connectors behind the Integration Service, which also checks
authorization and injects credentials. This stage is the same either way.
"""

from __future__ import annotations

import asyncio

from agent_harness.tools import ToolContext
from agent_harness.types import ToolUseBlock

from core_python.agent_runtime.harness.state import ActionResult, AgentState, EmployeeSpec


def idempotency_key(state: AgentState, call_idx: int) -> str:
    return f"{state.run_id}:{state.step}:{call_idx}"


def wrap_untrusted(source: str, content: str) -> str:
    """Mark tool output as data. A closing tag inside the content cannot end the block."""
    safe = content.replace("</data>", "&lt;/data&gt;")
    return f'<data source="{source}" trust="untrusted">\n{safe}\n</data>'


def batches(spec: EmployeeSpec, calls: list[ToolUseBlock]) -> list[list[int]]:
    """Group call indexes in order: consecutive reads share a batch, any other call is alone."""
    groups: list[list[int]] = []
    last_was_read = False
    for idx, call in enumerate(calls):
        is_read = spec.tier(call.name) == "R0"
        if is_read and last_was_read:
            groups[-1].append(idx)
        else:
            groups.append([idx])
        last_was_read = is_read
    return groups


async def act(
    spec: EmployeeSpec, state: AgentState, calls: list[ToolUseBlock]
) -> list[ActionResult]:
    """Run the calls. Returns one result per call, in the same order as `calls`."""
    results: list[ActionResult | None] = [None] * len(calls)
    gate = asyncio.Semaphore(spec.limits.max_parallel_reads)

    async def run_one(idx: int) -> None:
        call = calls[idx]
        key = idempotency_key(state, idx)
        ctx = ToolContext(
            agent=spec.name,
            run_id=state.run_id,
            step=state.step,
            state={
                "idempotency_key": key,
                "tenant_id": state.tenant_id,
                "employee_id": state.employee_id,
                "mode": state.mode,
            },
        )
        async with gate:
            outcome = await spec.tools.get(call.name).run(call.id, call.input, ctx)
        results[idx] = ActionResult(
            call_id=call.id,
            name=call.name,
            content=wrap_untrusted(f"tool:{call.name}", outcome.content),
            is_error=outcome.is_error,
            duration_ms=outcome.duration_ms,
            idempotency_key=key,
        )

    for batch in batches(spec, calls):
        await asyncio.gather(*(run_one(i) for i in batch))

    return [r for r in results if r is not None]
