"""The data that flows between harness stages.

Everything here except `EmployeeSpec` is a pydantic model that round-trips through JSON, so
it can live in Temporal workflow state and activity inputs and outputs. `EmployeeSpec` holds
live objects (tools, guardrails) and is rebuilt from the pinned employee version each time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Literal

from agent_harness import Guardrails
from agent_harness.tools import Tool, ToolRegistry
from agent_harness.types import Message, ToolResultBlock, ToolUseBlock, Usage
from pydantic import BaseModel, ConfigDict, Field

Tier = Literal["R0", "R1", "R2", "R3"]
"""Risk tier of a tool (agent-harness.md, stage 4).

R0 read-internal · R1 write-internal · R2 external communication · R3 irreversible or financial.
"""

Status = Literal["running", "waiting_human", "completed", "failed"]

StopReason = Literal[
    "",  # still running
    "final",  # the answer passed every check
    "give_up",  # the model or the harness could not make progress
    "max_steps",
    "max_cost",
    "budget",  # the next call would overspend
    "loop",  # the same call repeated too often
    "approval",  # waiting for a person to approve a call
    "guardrail",  # an answer was blocked and could not be repaired
]


class Limits(BaseModel):
    """Bounds on one run. Everything is bounded (agent-harness.md, design rule 4)."""

    max_steps: int = Field(default=20, ge=1)
    max_cost_usd: float = Field(default=1.0, gt=0)
    max_repairs: int = Field(default=2, ge=0)  # repair attempts per step
    loop_threshold: int = Field(default=3, ge=2)  # same call this many times = a loop
    max_parallel_reads: int = Field(default=5, ge=1)
    max_output_tokens: int = Field(default=4096, ge=1)
    context_tokens: int = Field(default=100_000, ge=1_000)  # history budget before compaction


class AgentState(BaseModel):
    """Everything needed to rebuild the next step. Nothing else is carried between steps."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    tenant_id: str
    employee_id: str
    step: int = 0
    messages: list[Message] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    #: Repair attempts used on the current step. Reset when a step makes progress.
    repairs: int = 0
    #: How many times each tool call (name + arguments) has been requested. Loop detector.
    call_counts: dict[str, int] = Field(default_factory=dict)
    status: Status = "running"
    stop_reason: StopReason = ""
    output: str | None = None
    #: Calls waiting for a person. Set when status is waiting_human because of an approval.
    pending: list[ToolUseBlock] = Field(default_factory=list)
    pending_reason: str = ""

    @property
    def done(self) -> bool:
        return self.status in ("completed", "failed")

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str | bytes) -> AgentState:
        return cls.model_validate_json(raw)

    @classmethod
    def start(cls, *, run_id: str, tenant_id: str, employee_id: str, task: str) -> AgentState:
        # ts=0: the clock never enters state, so the same inputs give the same state.
        return cls(
            run_id=run_id,
            tenant_id=tenant_id,
            employee_id=employee_id,
            messages=[Message(role="user", content=Message.user(task).content, ts=0.0)],
        )


# ---- decisions ---------------------------------------------------------------------------
# `decide` turns one model response into exactly one of these.


class Final(BaseModel):
    kind: Literal["final"] = "final"
    text: str


class ToolCalls(BaseModel):
    kind: Literal["tool_calls"] = "tool_calls"
    calls: list[ToolUseBlock]


class AskHuman(BaseModel):
    kind: Literal["ask_human"] = "ask_human"
    reason: str
    stop_reason: StopReason = "approval"
    #: For an approval: the calls a person must approve. Their siblings in the same step
    #: wait with them, so the step runs as a whole once decided.
    calls: list[ToolUseBlock] = Field(default_factory=list)


class GiveUp(BaseModel):
    kind: Literal["give_up"] = "give_up"
    reason: str
    stop_reason: StopReason = "give_up"


class Repair(BaseModel):
    """Internal: the response was invalid, ask the model again with the problem explained.

    `results` answers every tool call in the bad response (providers reject an unanswered
    call); `note` is said to the model when the response had no tool calls.
    """

    kind: Literal["repair"] = "repair"
    results: list[ToolResultBlock] = Field(default_factory=list)
    note: str = ""


Decision = Annotated[Final | ToolCalls | AskHuman | GiveUp | Repair, Field(discriminator="kind")]


class ActionResult(BaseModel):
    """One executed tool call, as recorded in the run ledger."""

    call_id: str
    name: str
    content: str
    is_error: bool = False
    duration_ms: float = 0.0
    idempotency_key: str

    def as_block(self) -> ToolResultBlock:
        return ToolResultBlock(
            tool_use_id=self.call_id, content=self.content, is_error=self.is_error
        )


# ---- the employee ------------------------------------------------------------------------


@dataclass
class EmployeeSpec:
    """One employee version at run time: who it is, its model, its granted tools and rules."""

    name: str
    instructions: str
    model: str
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    #: Risk tier per tool name. A tool without one is treated as R1 (write-internal).
    tiers: dict[str, Tier] = field(default_factory=dict)
    #: R2 tools allowed without approval (targets on the allowlist). R3 always asks.
    auto_approve: set[str] = field(default_factory=set)
    guardrails: Guardrails = field(default_factory=Guardrails)
    limits: Limits = field(default_factory=Limits)

    @classmethod
    def build(cls, *, tools: list[Tool] | None = None, **kw: object) -> EmployeeSpec:
        return cls(tools=ToolRegistry(tools or []), **kw)  # type: ignore[arg-type]

    def tier(self, tool_name: str) -> Tier:
        return self.tiers.get(tool_name, "R1")

    def needs_approval(self, tool: Tool) -> bool:
        """R3 always, R2 unless allowlisted, and any tool declared permission="ask"."""
        tier = self.tier(tool.name)
        if tier == "R3" or tool.permission == "ask":
            return True
        return tier == "R2" and tool.name not in self.auto_approve
