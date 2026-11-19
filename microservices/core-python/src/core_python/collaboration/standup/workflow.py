"""Standup: the co-CEO's standup, as a durable workflow (workflow id `standup-<standup id>`).

1. Roster: the co-CEO leads; the whole company or one department reports.
2. Updates from real task status, in batches of 5. Nothing new: a fixed line, no model call.
3. Discussion: the person can post; each post gets an answer from the co-CEO. It closes after
   `discussion_s` of quiet, or when the person ends it. A post signals the workflow, but the
   workflow always reads the messages themselves, so a lost signal only delays an answer.
4. Summary: where we are, where we go, next steps with owners, risks. With
   `assign_next_steps`, each step becomes a task for its owner.

Signals: person_posted (something was said; read it), end_discussion.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from core_python.collaboration.domain.standup import UPDATE_BATCH, Report, Summary
    from core_python.collaboration.standup.types import (
        FailInput,
        FinishInput,
        ReplyInput,
        Roster,
        StandupInput,
        StatusInput,
        UpdateInput,
    )

RETRY = RetryPolicy(maximum_attempts=4, initial_interval=timedelta(seconds=1))


async def _do(name: str, arg: Any, result_type: Any, timeout_s: float = 120) -> Any:
    return await workflow.execute_activity(
        name,
        arg,
        result_type=result_type,
        start_to_close_timeout=timedelta(seconds=timeout_s),
        retry_policy=RETRY,
    )


@workflow.defn(name="Standup")
class Standup:
    def __init__(self) -> None:
        self._posted = 0
        self._ended = False
        self._stage = "collecting"

    @workflow.signal
    def person_posted(self) -> None:
        self._posted += 1

    @workflow.signal
    def end_discussion(self) -> None:
        self._ended = True

    @workflow.query
    def stage(self) -> str:
        return self._stage

    @workflow.run
    async def run(self, inp: StandupInput) -> Summary | None:
        try:
            roster: Roster = await _do("standup_roster", inp, Roster)
            since: datetime = await _do("standup_since", inp, datetime)
            reports: list[Report] = []
            for i in range(0, len(roster.members), UPDATE_BATCH):
                batch = roster.members[i : i + UPDATE_BATCH]
                reports += await asyncio.gather(
                    *(
                        _do("standup_update", UpdateInput(inp=inp, member=m, since=since), Report)
                        for m in batch
                    )
                )
            if inp.discussion_s > 0:
                await self._discuss(inp, roster, reports)
            self._stage = "summarising"
            await _do("standup_status", StatusInput(inp=inp, status="summarising"), type(None))
            summary: Summary = await _do(
                "standup_finish",
                FinishInput(inp=inp, ceo=roster.ceo, members=roster.members, reports=reports),
                Summary,
                timeout_s=300,
            )
            self._stage = "done"
            return summary
        except ActivityError as exc:
            cause = exc.cause
            why = cause.message if isinstance(cause, ApplicationError) else str(cause or exc)
            self._stage = "failed"
            await _do("standup_fail", FailInput(inp=inp, error=why), type(None))
            return None

    async def _discuss(self, inp: StandupInput, roster: Roster, reports: list[Report]) -> None:
        self._stage = "discussing"
        note = (
            "The updates are in. The CEO can ask questions now; the discussion closes after "
            f"{inp.discussion_s} seconds of quiet."
        )
        await _do(
            "standup_status", StatusInput(inp=inp, status="discussing", note=note), type(None)
        )
        seen, last_seq = 0, 0
        while not self._ended:
            try:
                await workflow.wait_condition(
                    lambda seen=seen: self._posted > seen or self._ended,
                    timeout=timedelta(seconds=inp.discussion_s),
                )
            except TimeoutError:
                break
            seen = self._posted
            last_seq = await _do(
                "standup_reply",
                ReplyInput(inp=inp, ceo=roster.ceo, reports=reports, after_seq=last_seq),
                int,
            )
        # Anything said whose signal was lost still gets its answer.
        await _do(
            "standup_reply",
            ReplyInput(inp=inp, ceo=roster.ceo, reports=reports, after_seq=last_seq),
            int,
        )
