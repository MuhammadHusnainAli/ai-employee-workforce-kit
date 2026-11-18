"""BrainService over gRPC (contracts/proto/wfk/brain_service/v1/brain.proto)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import NoReturn

import grpc
from google.protobuf.timestamp_pb2 import Timestamp

from wfk.brain_service.v1 import brain_pb2, brain_pb2_grpc
from wfk.common.v1 import common_pb2

from brain_service.adapters.postgres.inspect import Inspected, Inspector, NotFound, Precondition
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


STATUS_PB = {
    "active": brain_pb2.MEMORY_STATUS_ACTIVE,
    "quarantined": brain_pb2.MEMORY_STATUS_QUARANTINED,
    "rejected": brain_pb2.MEMORY_STATUS_REJECTED,
}
STATUS_NAME = {v: k for k, v in STATUS_PB.items()}
KINDS = {"episodic", "semantic", "reflection"}
DEFAULT_PAGE, MAX_PAGE = 50, 200


def _ts(value: datetime | None) -> Timestamp | None:
    if value is None:
        return None
    ts = Timestamp()
    ts.FromDatetime(value)
    return ts


def _inspected(m: Inspected) -> brain_pb2.InspectedMemory:
    return brain_pb2.InspectedMemory(
        memory_id=str(m.id),
        employee_id=m.employee_id or "",
        kind=m.kind,
        content=m.content,
        status=STATUS_PB.get(m.status, brain_pb2.MEMORY_STATUS_UNSPECIFIED),
        trust=m.trust,
        confidence=m.confidence,
        importance=m.importance,
        fact_key=m.fact_key or "",
        source_ref=m.source_ref or "",
        valid_from=_ts(m.valid_from),
        valid_to=_ts(m.valid_to),
        recorded_at=_ts(m.recorded_at),
        superseded_by=str(m.superseded_by) if m.superseded_by else "",
        findings=m.findings,
        contradicts=m.contradicts,
        reinforced=m.reinforced,
        use_count=m.use_count,
    )


async def _tenant(tenant_id: str, context: grpc.aio.ServicerContext) -> str:
    try:
        return str(uuid.UUID(tenant_id))
    except ValueError:
        await _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "tenant.tenant_id must be a UUID")


async def _memory_id(value: str, context: grpc.aio.ServicerContext) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        await _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "memory_id must be a UUID")


def _actor(tenant: common_pb2.TenantContext) -> str:
    return tenant.actor.id or "unknown"


class BrainServicer(brain_pb2_grpc.BrainServiceServicer):
    def __init__(self, recall: Recall, inspector: Inspector) -> None:
        self.recall_service = recall
        self.inspector = inspector

    async def _guard[T](self, context: grpc.aio.ServicerContext, call: Awaitable[T]) -> T:
        """Map the Inspector's errors to gRPC codes."""
        try:
            return await call
        except NotFound as exc:
            await _abort(context, grpc.StatusCode.NOT_FOUND, str(exc))
        except Precondition as exc:
            await _abort(context, grpc.StatusCode.FAILED_PRECONDITION, str(exc))

    # ---- Brain Inspector -------------------------------------------------------------------

    async def ListMemories(
        self, request: brain_pb2.ListMemoriesRequest, context: grpc.aio.ServicerContext
    ) -> brain_pb2.ListMemoriesResponse:
        tenant = await _tenant(request.tenant.tenant_id, context)
        bad = grpc.StatusCode.INVALID_ARGUMENT
        if not request.employee_id:
            await _abort(context, bad, "employee_id is required")
        if not set(request.kinds) <= KINDS:
            await _abort(context, bad, f"kinds must be among {sorted(KINDS)}")
        size = request.page.page_size or DEFAULT_PAGE
        if not 0 < size <= MAX_PAGE:
            await _abort(context, bad, f"page_size must be 1..{MAX_PAGE}")
        before = None
        if request.page.page_token:
            try:
                before = datetime.fromisoformat(request.page.page_token)
            except ValueError:
                await _abort(context, bad, "bad page_token")
        rows = await self.inspector.list(
            tenant,
            request.employee_id,
            kinds=list(request.kinds),
            statuses=[STATUS_NAME[s] for s in request.statuses if s in STATUS_NAME],
            include_history=request.include_history,
            limit=size + 1,
            before=before,
        )
        more = len(rows) > size
        rows = rows[:size]
        token = rows[-1].recorded_at.isoformat() if more and rows else ""
        return brain_pb2.ListMemoriesResponse(
            memories=[_inspected(m) for m in rows],
            page=common_pb2.PageResponse(next_page_token=token),
        )

    async def GetMemory(
        self, request: brain_pb2.GetMemoryRequest, context: grpc.aio.ServicerContext
    ) -> brain_pb2.GetMemoryResponse:
        tenant = await _tenant(request.tenant.tenant_id, context)
        mid = await _memory_id(request.memory_id, context)
        d = await self._guard(context, self.inspector.get(tenant, mid))
        resp = brain_pb2.GetMemoryResponse(
            memory=_inspected(d.memory), replaced=[_inspected(m) for m in d.replaced]
        )
        if d.replaced_by:
            resp.replaced_by.CopyFrom(_inspected(d.replaced_by))
        if d.episode:
            resp.episode.CopyFrom(
                brain_pb2.Episode(
                    run_id=d.episode.run_id or "",
                    task=d.episode.task,
                    outcome=d.episode.outcome,
                    ended_at=_ts(d.episode.ended_at),
                )
            )
        return resp

    async def CorrectMemory(
        self, request: brain_pb2.CorrectMemoryRequest, context: grpc.aio.ServicerContext
    ) -> brain_pb2.CorrectMemoryResponse:
        tenant = await _tenant(request.tenant.tenant_id, context)
        mid = await _memory_id(request.memory_id, context)
        bad = grpc.StatusCode.INVALID_ARGUMENT
        key = request.idempotency_key.value.strip()
        if not key or len(key) > 200:
            await _abort(context, bad, "idempotency_key is required (at most 200 characters)")
        content = request.content.strip()
        if not content or len(content) > 4000:
            await _abort(context, bad, "content is required (at most 4000 characters)")
        m = await self._guard(
            context,
            self.inspector.correct(tenant, mid, content, by=_actor(request.tenant), key=key),
        )
        return brain_pb2.CorrectMemoryResponse(memory=_inspected(m))

    async def DeleteMemory(
        self, request: brain_pb2.DeleteMemoryRequest, context: grpc.aio.ServicerContext
    ) -> brain_pb2.DeleteMemoryResponse:
        tenant = await _tenant(request.tenant.tenant_id, context)
        mid = await _memory_id(request.memory_id, context)
        await self._guard(
            context,
            self.inspector.delete(tenant, mid, by=_actor(request.tenant), reason=request.reason),
        )
        return brain_pb2.DeleteMemoryResponse()

    async def ReviewMemory(
        self, request: brain_pb2.ReviewMemoryRequest, context: grpc.aio.ServicerContext
    ) -> brain_pb2.ReviewMemoryResponse:
        tenant = await _tenant(request.tenant.tenant_id, context)
        mid = await _memory_id(request.memory_id, context)
        review = self.inspector.review(
            tenant, mid, approve=request.approve, by=_actor(request.tenant), note=request.note
        )
        m = await self._guard(context, review)
        return brain_pb2.ReviewMemoryResponse(memory=_inspected(m))

    # ---- Recall ------------------------------------------------------------------------------

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
