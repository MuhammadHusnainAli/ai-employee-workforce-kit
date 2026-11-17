"""The activity contract: timeout, heartbeat and retry policy for every activity.

Values from the "Activity contract: timeouts & retries" table on diagram page 2. Every call
into an activity uses one of these; there is no activity without a timeout and a policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio.common import RetryPolicy

from agent_runtime.workflows.types import TASK_QUEUE_LLM, TASK_QUEUE_RUNS, TASK_QUEUE_TOOLS


@dataclass(frozen=True)
class Contract:
    queue: str
    start_to_close: timedelta
    retry: RetryPolicy
    heartbeat: timedelta | None = None
    #: Total time across all attempts. None: bounded by maximum_attempts instead.
    schedule_to_close: timedelta | None = None


def _s(seconds: float) -> timedelta:
    return timedelta(seconds=seconds)


CONTRACTS: dict[str, Contract] = {
    # LoadBlueprint: 10 s, no heartbeat, 1 s -> 30 s until 5 min. Non-retryable: not found.
    "load_employee": Contract(
        queue=TASK_QUEUE_RUNS,
        start_to_close=_s(10),
        schedule_to_close=timedelta(minutes=5),
        retry=RetryPolicy(
            initial_interval=_s(1),
            maximum_interval=_s(30),
            maximum_attempts=0,  # unlimited, bounded by schedule_to_close
            non_retryable_error_types=["EmployeeNotFound"],
        ),
    ),
    # RecallContext: 20 s, no heartbeat, 1 s -> 20 s, 5 attempts.
    "build_context": Contract(
        queue=TASK_QUEUE_RUNS,
        start_to_close=_s(20),
        retry=RetryPolicy(initial_interval=_s(1), maximum_interval=_s(20), maximum_attempts=5),
    ),
    # CallModel: 180 s, heartbeat 15 s, 2 s -> 60 s, 8 attempts, 429 / 5xx only.
    "call_model": Contract(
        queue=TASK_QUEUE_LLM,
        start_to_close=_s(180),
        heartbeat=_s(15),
        retry=RetryPolicy(
            initial_interval=_s(2),
            maximum_interval=_s(60),
            maximum_attempts=8,
            non_retryable_error_types=[
                "InvalidRequestError",
                "ModelNotFoundError",
                "ContextWindowExceededError",
                "AuthenticationError",
                "QuotaExceededError",
                "BudgetWouldExceed",
                "ContentPolicy",
                "EmployeeNotFound",
            ],
        ),
    ),
    # decide: pure checks, cheap. Same envelope as LoadBlueprint, short.
    "decide": Contract(
        queue=TASK_QUEUE_RUNS,
        start_to_close=_s(10),
        retry=RetryPolicy(initial_interval=_s(1), maximum_interval=_s(10), maximum_attempts=3),
    ),
    # ExecuteTool: per connector (<= 120 s), heartbeat 10 s, 1 s -> 60 s, 5 attempts, with the
    # same idempotency key. Non-retryable: ValidationError, UnknownOutcome (-> reconcile).
    "act": Contract(
        queue=TASK_QUEUE_TOOLS,
        start_to_close=_s(120),
        heartbeat=_s(10),
        retry=RetryPolicy(
            initial_interval=_s(1),
            maximum_interval=_s(60),
            maximum_attempts=5,
            non_retryable_error_types=["ValidationError", "UnknownOutcome", "EmployeeNotFound"],
        ),
    ),
}

# The run ledger writes: 10 s, 1 s -> 30 s until 5 min (a database blip must not fail a run).
_LEDGER = Contract(
    queue=TASK_QUEUE_RUNS,
    start_to_close=_s(10),
    schedule_to_close=timedelta(minutes=5),
    retry=RetryPolicy(initial_interval=_s(1), maximum_interval=_s(30), maximum_attempts=0),
)
CONTRACTS["record_start"] = _LEDGER
CONTRACTS["record_step"] = _LEDGER
