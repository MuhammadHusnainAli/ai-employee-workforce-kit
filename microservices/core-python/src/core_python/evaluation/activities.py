"""EvalProposal's activities, and the handler that starts an evaluation per proposal."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

from agent_harness.evaluation import EvalReport, Evaluator, GoldenTask
from temporalio import activity
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from wfk_platform.db import Conn
from wfk_platform.outbox import CloudEvent

from core_python.evaluation.adapters.agent import EmployeeAgent
from core_python.evaluation.adapters.stores import BrainEpisodes, Evals
from core_python.evaluation.domain import suites
from core_python.evaluation.workflows import (
    TASK_QUEUE,
    EvalProposal,
    Proposal,
    SuiteRun,
    Verdict,
    workflow_id,
)

log = logging.getLogger(__name__)
HEARTBEAT_S = 20


class EvalActivities:
    def __init__(
        self,
        client: Client,
        evals: Evals,
        brain: BrainEpisodes | None,
        *,
        suites_dir: Path,
        run_queue: str,
        run_timeout_s: float,
        concurrency: int,
    ) -> None:
        self.client = client
        self.evals = evals
        self.brain = brain
        self.suites_dir = suites_dir
        self.run_queue = run_queue
        self.run_timeout_s = run_timeout_s
        self.concurrency = concurrency

    @activity.defn(name="eval_build_suite")
    async def build_suite(self, p: Proposal) -> list[dict[str, Any]]:
        await self.evals.start(p.tenant_id, p.employee_id, p.proposal_id)
        tasks = suites.suite(self.suites_dir, p.employee_id)
        if self.brain is not None:
            try:
                past = await self.brain.successes(p.tenant_id, p.employee_id)
                tasks += suites.regression(past)
            except Exception as exc:  # no regression set beats no evaluation
                log.warning("regression set unavailable", extra={"error": str(exc)})
        return [t.model_dump(mode="json", exclude={"grader"}) for t in tasks]

    @activity.defn(name="eval_run_suite")
    async def run_suite(self, s: SuiteRun) -> dict[str, Any]:
        agent = EmployeeAgent(
            self.client,
            tenant_id=s.proposal.tenant_id,
            employee_id=s.proposal.employee_id,
            queue=self.run_queue,
            candidate=s.candidate,
            timeout_s=self.run_timeout_s,
        )
        tasks = [GoldenTask.model_validate(t) for t in s.tasks]

        async def beat() -> None:
            while True:
                activity.heartbeat(s.label)
                await asyncio.sleep(HEARTBEAT_S)

        beating = asyncio.create_task(beat())
        try:
            report = await Evaluator(tasks, concurrency=self.concurrency).run(agent, label=s.label)
        finally:
            beating.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beating
        return report.model_dump(mode="json")

    @activity.defn(name="eval_finish")
    async def finish(self, args: dict[str, Any]) -> Verdict:
        p = Proposal.model_validate(args["proposal"])
        baseline = EvalReport.model_validate(args["baseline"])
        candidate = EvalReport.model_validate(args["candidate"])
        passed, reasons = suites.verdict(baseline, candidate)
        await self.evals.finish(
            p.tenant_id, p.employee_id, p.proposal_id,
            passed=passed, reasons=reasons,
            baseline=args["baseline"], candidate=args["candidate"],
            comparison=candidate.compare(baseline).model_dump(mode="json"),
        )  # fmt: skip
        return Verdict(passed=passed, reasons=reasons)


class ProposalsToEval:
    """Inbox handler on the BRAIN stream: every proposed change is evaluated once."""

    def __init__(self, client: Client, task_queue: str = TASK_QUEUE) -> None:
        self.client = client
        self.task_queue = task_queue

    async def __call__(self, conn: Conn, event: CloudEvent) -> None:
        if event.type != "brain.change_proposed":
            return
        p = Proposal(
            tenant_id=str(event.tenantid),
            employee_id=str(event.data.get("employee_id") or ""),
            proposal_id=str(event.data.get("proposal_id") or ""),
        )
        if not p.employee_id or not p.proposal_id:
            return
        try:
            await self.client.start_workflow(
                EvalProposal.run, p, id=workflow_id(p), task_queue=self.task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )  # fmt: skip
        except WorkflowAlreadyStartedError:
            pass
