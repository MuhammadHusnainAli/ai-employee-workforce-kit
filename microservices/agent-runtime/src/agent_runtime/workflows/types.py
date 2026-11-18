"""Inputs and outputs of the EmployeeRun workflow and its activities.

All pydantic, sent through Temporal with the pydantic data converter, so they are recorded in
workflow history exactly as sent.
"""

from __future__ import annotations

from typing import Any, Literal

from agent_harness.llm_providers import CompletionRequest
from agent_harness.types import ModelResponse, ToolUseBlock
from pydantic import BaseModel, Field

from agent_runtime.harness.state import AgentState, Decision, Limits

TASK_QUEUE_RUNS = "runs"
TASK_QUEUE_LLM = "llm"
TASK_QUEUE_TOOLS = "tools"


RunMode = Literal["live", "eval", "shadow"]


class RunInput(BaseModel):
    run_id: str
    tenant_id: str
    employee_id: str
    task: str
    #: How long the run waits for a person before it gives up waiting. An approval that
    #: expires counts as declined; an unanswered question fails the run.
    wait_timeout_s: float = Field(default=7 * 24 * 3600, gt=0)
    #: live: real work. eval: an evaluation run (isolated queue). shadow: a candidate running
    #: beside live work, whose risky tool calls are never executed. Only live runs are
    #: remembered, credited, or answered back to a person.
    mode: RunMode = "live"
    #: A proposed brain change (proposal id) this run uses as if it were already active.
    candidate: str = ""


class EmployeeInfo(BaseModel):
    """The employee version pinned at run start. Edits mid-run never change this run."""

    employee_id: str
    name: str
    model: str
    limits: Limits


class StepInput(BaseModel):
    employee_id: str
    limits: Limits
    state: AgentState


class ModelInput(BaseModel):
    employee_id: str
    limits: Limits
    request: CompletionRequest
    state: AgentState


class DecideInput(BaseModel):
    employee_id: str
    limits: Limits
    state: AgentState
    response: ModelResponse


class DecisionEnvelope(BaseModel):
    decision: Decision


class ActInput(BaseModel):
    employee_id: str
    limits: Limits
    state: AgentState
    calls: list[ToolUseBlock]


class ApprovalAnswer(BaseModel):
    approved: bool
    by: str
    note: str = ""


class RunOutcome(BaseModel):
    state: AgentState
    ledger_entries: int = Field(default=0, description="steps written to run_steps")


# ---- the run ledger ----------------------------------------------------------------------


class StartRecord(BaseModel):
    run_id: str
    tenant_id: str
    employee_id: str
    task: str
    mode: RunMode = "live"
    candidate: str = ""


class LedgerEntry(BaseModel):
    """One recorded transition of a run: a step, an approval, a cancel. Written once."""

    run_id: str
    tenant_id: str
    employee_id: str
    seq: int  # assigned by the workflow, so a retried write finds its own row
    kind: str  # decision kind, or approved / declined / reply / budget / error / cancelled
    decision: dict[str, Any] = Field(default_factory=dict)
    results: list[dict[str, Any]] = Field(default_factory=list)
    model: str | None = None
    prompt_hash: str | None = None
    recall_id: str | None = None  # the brain recall this step's prompt cited
    memory_degraded: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    # the run after this entry
    step: int
    status: str
    stop_reason: str = ""
    output: str | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    mode: RunMode = "live"
