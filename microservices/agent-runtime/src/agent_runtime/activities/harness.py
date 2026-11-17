"""The harness stages as Temporal activities.

Each activity rebuilds the employee from the directory, applies the limits pinned at run
start, and runs one stage. Stage logic lives in agent_runtime.harness; this module only
adapts it to Temporal: inputs, outputs and how errors are reported.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_harness.llm_providers import Provider
from agent_harness.types import ModelResponse
from temporalio import activity
from temporalio.exceptions import ApplicationError

from agent_runtime.activities.heartbeat import heartbeating
from agent_runtime.employees import EmployeeDirectory, EmployeeNotFound
from agent_runtime.harness import act as act_stage
from agent_runtime.harness import context as context_stage
from agent_runtime.harness import model as model_stage
from agent_runtime.harness import validate as validate_stage
from agent_runtime.harness.state import ActionResult, EmployeeSpec, Limits
from agent_runtime.workflows.types import (
    ActInput,
    DecideInput,
    DecisionEnvelope,
    EmployeeInfo,
    ModelInput,
    StepInput,
)


@dataclass
class HarnessActivities:
    employees: EmployeeDirectory
    provider: Provider

    def _spec(self, employee_id: str, limits: Limits | None = None) -> EmployeeSpec:
        try:
            spec = self.employees.get(employee_id)
        except EmployeeNotFound as exc:
            raise ApplicationError(str(exc), type="EmployeeNotFound", non_retryable=True) from exc
        if limits is not None:
            spec.limits = limits  # pinned at run start
        return spec

    @activity.defn(name="load_employee")
    async def load_employee(self, employee_id: str) -> EmployeeInfo:
        spec = self._spec(employee_id)
        return EmployeeInfo(
            employee_id=employee_id, name=spec.name, model=spec.model, limits=spec.limits
        )

    @activity.defn(name="build_context")
    async def build_context(self, inp: StepInput) -> context_stage.BuiltContext:
        return await context_stage.build_context(self._spec(inp.employee_id, inp.limits), inp.state)

    @activity.defn(name="call_model")
    async def call_model(self, inp: ModelInput) -> ModelResponse:
        spec = self._spec(inp.employee_id, inp.limits)
        try:
            async with heartbeating():
                response = await model_stage.call_model(
                    self.provider, inp.request, inp.state, spec.limits
                )
        except Exception as exc:
            if model_stage.classify(exc) == "non_retryable":
                raise ApplicationError(
                    str(exc), type=type(exc).__name__, non_retryable=True
                ) from exc
            raise
        # The raw provider payload is large and not needed past this point.
        return response.model_copy(update={"raw": None})

    @activity.defn(name="decide")
    async def decide(self, inp: DecideInput) -> DecisionEnvelope:
        spec = self._spec(inp.employee_id, inp.limits)
        return DecisionEnvelope(decision=validate_stage.decide(spec, inp.state, inp.response))

    @activity.defn(name="act")
    async def act(self, inp: ActInput) -> list[ActionResult]:
        async with heartbeating():
            return await act_stage.act(
                self._spec(inp.employee_id, inp.limits), inp.state, inp.calls
            )

    def all(self) -> list[object]:
        return [self.load_employee, self.build_context, self.call_model, self.decide, self.act]
