"""Entry point: the whole Python backend in one process (ADR-0017).

`core-python` (or `core-python serve`) runs, until SIGINT / SIGTERM:

- one gRPC server on one port: health, WorkforceService, RunService, BrainService;
- workforce: the outbox relay (employee.* to EMPLOYEES);
- agent_runtime: the live Temporal workers (runs, llm, tools), the run outbox relay (RUNS)
  and the task inbox (TASKS); and the evaluation workers (eval-runs, eval-llm, eval-tools),
  workflows only, so evaluations never take live work;
- brain: brain-ingest (BrainIngest, nightly consolidation, RUNS and EVAL inboxes, BRAIN outbox);
- evaluation: EvalProposal, the brain.change_proposed inbox and the EVAL outbox.

Each background part is supervised: if one fails (Temporal or NATS down), it is logged and
restarted after a pause, and the gRPC server keeps serving.

`core-python migrate` applies every module's migrations to its own database.
`core-python demo-run "text"` runs agent_runtime's durable demo.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from wfk_platform import configure_logging, setup_telemetry
from wfk_platform.messaging import run_outbox_relay

from core_python import settings as config
from core_python.app import Databases, create_server

log = logging.getLogger(__name__)

#: Module name -> (migrations/<folder>, settings field with its database).
MODULES = {
    "workforce": "workforce_database_url",
    "agent_runtime": "runtime_database_url",
    "brain": "brain_database_url",
    "evaluation": "eval_database_url",
}

Part = Callable[[asyncio.Event], Awaitable[None]]


async def supervise(name: str, part: Part, stop: asyncio.Event, *, pause_s: float = 5.0) -> None:
    """Run `part` until `stop` is set, restarting it after a pause whenever it fails or ends.

    Each run gets its own stop event, so a part that sets it on the way out stops only itself.
    """
    while not stop.is_set():
        own = asyncio.Event()
        link = asyncio.create_task(_set_when(stop, own))
        try:
            await part(own)
            if not stop.is_set():
                log.warning("part ended, restarting", extra={"part": name})
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("part failed, restarting", extra={"part": name})
        finally:
            own.set()
            link.cancel()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), pause_s)


async def _set_when(source: asyncio.Event, target: asyncio.Event) -> None:
    await source.wait()
    target.set()


def parts(s: config.Settings, dbs: Databases) -> dict[str, Part]:
    """Every background part of the process, by name."""
    from core_python.agent_runtime.main import run_workers
    from core_python.brain.ingest import ingest
    from core_python.evaluation.main import evaluate
    from core_python.workforce.main import STREAM, SUBJECTS

    runtime, brain, evaluation = config.runtime(s), config.brain(s), config.evaluation(s)
    model = s.worker_model

    def employees(stop: asyncio.Event) -> Awaitable[None]:
        return run_outbox_relay(dbs.workforce, s.nats_url, stream=STREAM, subjects=SUBJECTS,
                                stop=stop)  # fmt: skip

    def live(stop: asyncio.Event) -> Awaitable[None]:
        return run_workers(runtime, prefix="", model=model, stop=stop, db=dbs.runtime)

    def evals(stop: asyncio.Event) -> Awaitable[None]:
        return run_workers(runtime, prefix="eval-", model=model, loops=False, stop=stop,
                           db=dbs.runtime)  # fmt: skip

    def remember(stop: asyncio.Event) -> Awaitable[None]:
        return ingest(brain, stop=stop, db=dbs.brain)

    def gate(stop: asyncio.Event) -> Awaitable[None]:
        return evaluate(evaluation, stop=stop, db=dbs.eval)

    named: dict[str, Part] = {"workforce-relay": employees}
    if s.workers:
        named |= {
            "runtime-workers": live,
            "runtime-eval-workers": evals,
            "brain-ingest": remember,
            "evaluation": gate,
        }
    return named


async def serve(s: config.Settings) -> None:
    configure_logging(s.service_name, s.log_level, json_output=s.log_json)
    telemetry = setup_telemetry(s)
    dbs = Databases.from_settings(s)
    # Do not wait for the databases: the service starts, and readiness says when it is usable.
    await dbs.open(wait=False)
    server, health, port = create_server(s, dbs)
    await server.start()
    log.info("started", extra={"port": port})

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows: Ctrl-C raises instead
            loop.add_signal_handler(sig, stop.set)
    running = [
        asyncio.create_task(supervise(name, part, stop), name=name)
        for name, part in parts(s, dbs).items()
    ]
    try:
        await stop.wait()
    finally:
        health.shutdown()  # NOT_SERVING first, so traffic drains
        stop.set()
        await server.stop(grace=5)
        await asyncio.gather(*running, return_exceptions=True)
        await dbs.close()
        telemetry.shutdown()


def migrate(s: config.Settings) -> None:
    """Apply every module's pending migrations, creating its database if missing."""
    from wfk_platform.migrate import Runner, ensure_database

    root = Path(s.migrations_dir)
    for module, field in MODULES.items():
        dsn = getattr(s, field)
        ensure_database(dsn)
        applied = Runner(dsn, root / module).up()
        print(f"{module}: applied {len(applied)} migration(s)", flush=True)


def run(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="core-python")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="the gRPC server and every worker (the default)")
    sub.add_parser("migrate", help="apply every module's migrations")
    demo = sub.add_parser("demo-run", help="a durable demo run you can watch")
    demo.add_argument("rest", nargs=argparse.REMAINDER, help='"text" [--model ...]')
    args = parser.parse_args(argv)

    if args.cmd == "demo-run":
        from core_python.agent_runtime.main import run as runtime_run

        runtime_run(["demo-run", *args.rest])
        return
    s = config.Settings()
    if args.cmd == "migrate":
        migrate(s)
        return
    # psycopg's async driver needs a selector loop; Windows defaults to Proactor.
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(s), loop_factory=factory)


if __name__ == "__main__":
    run()
