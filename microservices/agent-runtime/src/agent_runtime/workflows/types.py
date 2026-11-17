"""Inputs and outputs of the EmployeeRun workflow and its activities.

All pydantic, sent through Temporal with the pydantic data converter, so they are recorded in
workflow history exactly as sent.
"""

from __future__ import annotations

from agent_harness.llm_providers import CompletionRequest
from agent_harness.types import ModelResponse, ToolUseBlock
from pydantic import BaseModel, Field

from agent_runtime.harness.state import AgentState, Decision, Limits

TASK_QUEUE_RUNS = "runs"
TASK_QUEUE_LLM = "llm"
TASK_QUEUE_TOOLS = "tools"


class RunInput(BaseModel):
    run_id: str
    tenant_id: str
    employee_id: str
    task: str
    #: How long the run waits for a person before it gives up waiting. An approval that
    #: expires counts as declined; an unanswered question fails the run.
    wait_timeout_s: float = Field(default=7 * 24 * 3600, gt=0)


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
