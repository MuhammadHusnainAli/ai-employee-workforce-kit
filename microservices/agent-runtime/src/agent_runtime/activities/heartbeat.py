"""Heartbeat while a long activity waits on something else (a model call, a tool)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from temporalio import activity


@contextlib.asynccontextmanager
async def heartbeating(every_s: float = 5.0) -> AsyncIterator[None]:
    """Send a heartbeat every `every_s` seconds until the block ends.

    With a 15 s heartbeat timeout and a heartbeat every 5 s, a worker that dies is noticed
    within 15 s and the activity is retried elsewhere, instead of waiting for the full
    start-to-close timeout.
    """

    async def beat() -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(every_s)

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
