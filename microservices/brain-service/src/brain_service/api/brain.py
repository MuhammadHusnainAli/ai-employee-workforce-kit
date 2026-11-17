"""BrainService over gRPC (contracts/proto/wfk/brain_service/v1/brain.proto)."""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import NoReturn

import grpc
from google.protobuf.timestamp_pb2 import Timestamp

from wfk.brain_service.v1 import brain_pb2, brain_pb2_grpc

from brain_service.domain.recall.planner import StepType
from brain_service.recall import Recall, RecallRequest

STEP_TYPES: dict[int, StepType] = {
    brain_pb2.STEP_TYPE_UNSPECIFIED: "acting",
    brain_pb2.STEP_TYPE_PLANNING: "planning",
    brain_pb2.STEP_TYPE_ACTING: "acting",
    brain_pb2.STEP_TYPE_REPLYING: "replying",
    brain_pb2.STEP_TYPE_LIVE_MEETING: "live_meeting",
}
MAX_BUDGET = 32_000


async def _abort(context: grpc.aio.ServicerContext, code: grpc.StatusCode, msg: str) -> NoReturn:
    await context.abort(code, msg)
    raise AssertionError("unreachable")


class BrainServicer(brain_pb2_grpc.BrainServiceServicer):
    def __init__(self, recall: Recall) -> None:
        self.recall_service = recall

    async def Recall(
        self, request: brain_pb2.RecallRequest, context: grpc.aio.ServicerContext
    ) -> brain_pb2.RecallResponse:
        bad = grpc.StatusCode.INVALID_ARGUMENT
        try:
            tenant = str(uuid.UUID(request.tenant.tenant_id))
        except ValueError:
            await _abort(context, bad, "tenant.tenant_id must be a UUID")
        if not request.employee_id:
            await _abort(context, bad, "employee_id is required")
        if not request.query.strip():
            await _abort(context, bad, "query is required")
        if not 0 <= request.token_budget <= MAX_BUDGET:
            await _abort(context, bad, f"token_budget must be 0..{MAX_BUDGET}")

        result = await self.recall_service.recall(
            RecallRequest(
                tenant_id=tenant,
                employee_id=request.employee_id,
                query=request.query,
                step_type=STEP_TYPES.get(request.step_type, "acting"),
                token_budget=request.token_budget,
                run_id=request.run_id or None,
                step=request.step if request.run_id else None,
                as_of=request.as_of.ToDatetime(tzinfo=UTC) if request.HasField("as_of") else None,
            )
        )
        items = []
        for item in result.items:
            recorded = Timestamp()
            recorded.FromDatetime(item.memory.recorded_at)
            items.append(
                brain_pb2.RecalledItem(
                    memory_id=str(item.memory.id),
                    kind=item.memory.kind,
                    content=item.memory.content,
                    score=item.score,
                    trust=item.memory.trust,
                    recorded_at=recorded,
                    source_ref=item.memory.source_ref or "",
                )
            )
        return brain_pb2.RecallResponse(
            recall_id=result.recall_id,
            identity=result.identity,
            identity_version=result.identity_version,
            rules=result.rules,
            items=items,
            tokens_used=result.tokens_used,
            degraded=result.degraded,
            skipped=result.skipped,
        )
