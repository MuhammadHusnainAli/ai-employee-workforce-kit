"""EvalProposal: evaluate one proposed brain change, on task queue `eval`.

    build the suite  ->  run it without the change  ->  run it with the change  ->  verdict

Each step is an activity: a crash resumes after the last finished step, and a finished
evaluation is never run again (workflow id from the proposal, verdict stored once).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from pydantic import BaseModel

TASK_QUEUE = "eval"
RUN_SUITE = timedelta(minutes=30)
RETRY = RetryPolicy(maximum_attempts=3, maximum_interval=timedelta(minutes=1))


class Proposal(BaseModel):
    tenant_id: str
    employee_id: str
    proposal_id: str


class SuiteRun(BaseModel):
    proposal: Proposal
    tasks: list[dict[str, Any]]
    label: str
    candidate: str = ""


class Verdict(BaseModel):
    passed: bool
    reasons: list[str]


def workflow_id(p: Proposal) -> str:
    return f"eval-{p.proposal_id}"


@workflow.defn(name="EvalProposal")
class EvalProposal:
    @workflow.run
    async def run(self, p: Proposal) -> Verdict:
        tasks: list[dict[str, Any]] = await workflow.execute_activity(
            "eval_build_suite", p, result_type=list[dict[str, Any]],
            start_to_close_timeout=timedelta(minutes=2), retry_policy=RETRY,
        )  # fmt: skip
        opts: dict[str, Any] = {
            "start_to_close_timeout": RUN_SUITE,
            "heartbeat_timeout": timedelta(minutes=2),
            "retry_policy": RETRY,
        }
        baseline: dict[str, Any] = await workflow.execute_activity(
            "eval_run_suite", SuiteRun(proposal=p, tasks=tasks, label="baseline"),
            result_type=dict[str, Any], **opts,
        )  # fmt: skip
        candidate: dict[str, Any] = await workflow.execute_activity(
            "eval_run_suite",
            SuiteRun(proposal=p, tasks=tasks, label="candidate", candidate=p.proposal_id),
            result_type=dict[str, Any], **opts,
        )  # fmt: skip
        return await workflow.execute_activity(
            "eval_finish", {"proposal": p, "baseline": baseline, "candidate": candidate},
            result_type=Verdict, start_to_close_timeout=timedelta(minutes=2), retry_policy=RETRY,
        )  # fmt: skip
