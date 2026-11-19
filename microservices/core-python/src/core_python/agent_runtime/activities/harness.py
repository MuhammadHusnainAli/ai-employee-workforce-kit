"""The harness stages as Temporal activities.

Each activity rebuilds the employee from the directory, applies the limits pinned at run
start, and runs one stage. Stage logic lives in core_python.agent_runtime.harness; this module only
adapts it to Temporal: inputs, outputs and how errors are reported.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_harness.llm_providers import Provider
from agent_harness.types import ModelResponse
from temporalio import activity
from temporalio.exceptions import ApplicationError

from core_python.agent_runtime.activities.heartbeat import heartbeating
from core_python.agent_runtime.adapters.brain import BrainClient, BrainMemoryStore
from core_python.agent_runtime.employees import (
    EmployeeDirectory,
    EmployeeNotFound,
    EmployeeUnavailable,
)
from core_python.agent_runtime.harness import act as act_stage
from core_python.agent_runtime.harness import context as context_stage
from core_python.agent_runtime.harness import model as model_stage
from core_python.agent_runtime.harness import validate as validate_stage
from core_python.agent_runtime.harness.state import ActionResult, AgentState, EmployeeSpec, Limits
from core_python.agent_runtime.workflows.types import (
    ActInput,
    DecideInput,
    DecisionEnvelope,
    EmployeeInfo,
    LoadEmployee,
    ModelInput,
    StepInput,
)


@dataclass
class HarnessActivities:
    employees: EmployeeDirectory
    provider: Provider
    #: The employee's brain. None: runs without long-term memory.
    brain: BrainClient | None = None

    async def _spec(
        self,
        employee_id: str,
        limits: Limits | None = None,
        *,
        tenant_id: str = "",
        version: int = 0,
    ) -> EmployeeSpec:
        try:
            spec = await self.employees.get(employee_id, tenant_id=tenant_id, version=version)
        except EmployeeNotFound as exc:
            raise ApplicationError(str(exc), type="EmployeeNotFound", non_retryable=True) from exc
        except EmployeeUnavailable as exc:
            raise ApplicationError(str(exc), type="EmployeePaused", non_retryable=True) from exc
        if limits is not None:
            spec.limits = limits  # pinned at run start
        return spec

    async def _pinned(self, employee_id: str, limits: Limits, state: AgentState) -> EmployeeSpec:
        return await self._spec(
            employee_id, limits, tenant_id=state.tenant_id, version=state.employee_version
        )

    @activity.defn(name="load_employee")
    async def load_employee(self, who: LoadEmployee | str) -> EmployeeInfo:
        # A plain id: runs started before employees came from workforce-service.
        employee_id, tenant_id = (
            (who, "") if isinstance(who, str) else (who.employee_id, who.tenant_id)
        )
        spec = await self._spec(employee_id, tenant_id=tenant_id)
        return EmployeeInfo(
            employee_id=employee_id, name=spec.name, model=spec.model, limits=spec.limits,
            version=spec.version,
        )  # fmt: skip

    @activity.defn(name="build_context")
    async def build_context(self, inp: StepInput) -> context_stage.BuiltContext:
        memory = None
        if self.brain is not None:
            memory = BrainMemoryStore(
                self.brain,
                tenant_id=inp.state.tenant_id,
                employee_id=inp.employee_id,
                run_id=inp.state.run_id,
                step=inp.state.step,
                step_type="planning" if inp.state.step == 0 else "acting",
                candidate=inp.state.candidate,
            )
        return await context_stage.build_context(
            await self._pinned(inp.employee_id, inp.limits, inp.state), inp.state, memory
        )

    @activity.defn(name="call_model")
    async def call_model(self, inp: ModelInput) -> ModelResponse:
        spec = await self._pinned(inp.employee_id, inp.limits, inp.state)
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
        spec = await self._pinned(inp.employee_id, inp.limits, inp.state)
        return DecisionEnvelope(decision=validate_stage.decide(spec, inp.state, inp.response))

    @activity.defn(name="act")
    async def act(self, inp: ActInput) -> list[ActionResult]:
        async with heartbeating():
            return await act_stage.act(
                await self._pinned(inp.employee_id, inp.limits, inp.state), inp.state, inp.calls
            )

    def all(self) -> list[object]:
        return [self.load_employee, self.build_context, self.call_model, self.decide, self.act]
