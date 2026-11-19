"""RunService: read access to the run ledger (contracts/proto/wfk/agent_runtime/v1/runs.proto).

Every read is a tenant transaction, so row-level security decides what is visible. A run of
another tenant looks exactly like a run that does not exist: NOT_FOUND, nothing leaked.
"""

from __future__ import annotations

import base64
import uuid
from typing import Any, NoReturn

import grpc
from google.protobuf.struct_pb2 import Struct
from google.protobuf.timestamp_pb2 import Timestamp

from wfk.agent_runtime.v1 import runs_pb2, runs_pb2_grpc
from wfk.common.v1 import common_pb2
from wfk_platform.db import Database

DEFAULT_PAGE = 50
MAX_PAGE = 200

STATUS = {
    "running": runs_pb2.RUN_STATUS_RUNNING,
    "waiting_human": runs_pb2.RUN_STATUS_WAITING_HUMAN,
    "completed": runs_pb2.RUN_STATUS_COMPLETED,
    "failed": runs_pb2.RUN_STATUS_FAILED,
}


async def _abort(context: grpc.aio.ServicerContext, code: grpc.StatusCode, msg: str) -> NoReturn:
    await context.abort(code, msg)
    raise AssertionError("unreachable")  # abort raises


async def _tenant(tenant: common_pb2.TenantContext, context: grpc.aio.ServicerContext) -> str:
    try:
        return str(uuid.UUID(tenant.tenant_id))
    except ValueError:
        await _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "tenant.tenant_id must be a UUID")


def _ts(value: Any) -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(value)
    return ts


def _struct(value: dict[str, Any]) -> Struct:
    s = Struct()
    s.update(value)
    return s


def encode_token(seq: int) -> str:
    return base64.urlsafe_b64encode(f"seq:{seq}".encode()).decode()


def decode_token(token: str) -> int:
    """The last seq of the previous page. Raises ValueError on anything else."""
    raw = base64.urlsafe_b64decode(token.encode()).decode()
    prefix, _, seq = raw.partition(":")
    if prefix != "seq":
        raise ValueError(token)
    return int(seq)


class RunServicer(runs_pb2_grpc.RunServiceServicer):
    def __init__(self, db: Database) -> None:
        self.db = db

    async def GetRun(
        self, request: runs_pb2.GetRunRequest, context: grpc.aio.ServicerContext
    ) -> runs_pb2.GetRunResponse:
        tenant = await _tenant(request.tenant, context)
        if not request.run_id:
            await _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "run_id is required")
        async with self.db.tenant_tx(tenant) as conn:
            cur = await conn.execute("SELECT * FROM runs WHERE run_id = %s", (request.run_id,))
            row = await cur.fetchone()
        if row is None:
            await _abort(context, grpc.StatusCode.NOT_FOUND, f"no run {request.run_id!r}")
        return runs_pb2.GetRunResponse(
            run=runs_pb2.Run(
                run_id=row["run_id"],
                tenant_id=str(row["tenant_id"]),
                employee_id=row["employee_id"],
                task=row["task"],
                status=STATUS.get(row["status"], runs_pb2.RUN_STATUS_UNSPECIFIED),
                stop_reason=row["stop_reason"],
                output=row["output"] or "",
                steps=row["steps"],
                input_tokens=row["input_tokens"],
                output_tokens=row["output_tokens"],
                cost_usd=str(row["cost_usd"]),
                created_at=_ts(row["created_at"]),
                updated_at=_ts(row["updated_at"]),
            )
        )

    async def ListSteps(
        self, request: runs_pb2.ListStepsRequest, context: grpc.aio.ServicerContext
    ) -> runs_pb2.ListStepsResponse:
        tenant = await _tenant(request.tenant, context)
        size = request.page.page_size or DEFAULT_PAGE
        if not 0 < size <= MAX_PAGE:
            await _abort(
                context, grpc.StatusCode.INVALID_ARGUMENT, f"page_size must be 1..{MAX_PAGE}"
            )
        after = -1
        if request.page.page_token:
            try:
                after = decode_token(request.page.page_token)
            except ValueError:
                await _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "bad page_token")
        async with self.db.tenant_tx(tenant) as conn:
            cur = await conn.execute("SELECT 1 FROM runs WHERE run_id = %s", (request.run_id,))
            if await cur.fetchone() is None:
                await _abort(context, grpc.StatusCode.NOT_FOUND, f"no run {request.run_id!r}")
            cur = await conn.execute(
                "SELECT * FROM run_steps WHERE run_id = %s AND seq > %s ORDER BY seq LIMIT %s",
                (request.run_id, after, size + 1),
            )
            rows = await cur.fetchall()
        more = len(rows) > size
        rows = rows[:size]
        steps = [
            runs_pb2.Step(
                seq=r["seq"],
                step=r["step"],
                kind=r["kind"],
                status_after=STATUS.get(r["status_after"], runs_pb2.RUN_STATUS_UNSPECIFIED),
                model=r["model"] or "",
                prompt_hash=r["prompt_hash"] or "",
                input_tokens=r["input_tokens"],
                output_tokens=r["output_tokens"],
                cost_usd=str(r["cost_usd"]),
                decision=_struct(r["decision"]),
                results=[_struct(x) for x in r["results"]],
                created_at=_ts(r["created_at"]),
                recall_id=str(r["recall_id"]) if r["recall_id"] else "",
                memory_degraded=r["memory_degraded"],
            )
            for r in rows
        ]
        token = encode_token(rows[-1]["seq"]) if more and rows else ""
        return runs_pb2.ListStepsResponse(
            steps=steps, page=common_pb2.PageResponse(next_page_token=token)
        )
