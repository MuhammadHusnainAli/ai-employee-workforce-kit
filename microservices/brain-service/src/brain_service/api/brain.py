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

from brain_service.adapters.postgres.feedback import Feedback, Invalid
from brain_service.adapters.postgres.inspect import Inspected, Inspector, NotFound, Precondition
from brain_service.adapters.postgres.rules import Proposal, Rule, Rules
from brain_service.domain.feedback import FeedbackIn
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


FEEDBACK_KINDS = {
    brain_pb2.FEEDBACK_KIND_CORRECTION: "correction",
    brain_pb2.FEEDBACK_KIND_RATING: "rating",
    brain_pb2.FEEDBACK_KIND_APPROVAL_REJECTED: "approval_rejected",
    brain_pb2.FEEDBACK_KIND_COMMENT: "comment",
}
ACTOR_KINDS = {
    common_pb2.ACTOR_KIND_HUMAN: "human",
    common_pb2.ACTOR_KIND_EMPLOYEE: "employee",
    common_pb2.ACTOR_KIND_SERVICE: "service",
}


def _proposal(p: Proposal) -> brain_pb2.Proposal:
    return brain_pb2.Proposal(
        proposal_id=str(p.id),
        employee_id=p.employee_id,
        kind=p.kind,
        text=str(p.payload.get("text", "")),
        risk=p.risk,
        status=p.status,
        proposed_by=p.proposed_by,
        decided_by=p.decided_by or "",
        decision_note=p.decision_note or "",
        feedback_id=str(p.evidence.get("feedback_id") or ""),
        run_id=str(p.evidence.get("run_id") or ""),
        created_at=_ts(p.created_at),
        decided_at=_ts(p.decided_at),
        eval_status=p.eval_status,
        eval_reasons=p.eval_reasons,
    )


def _rule(r: Rule) -> brain_pb2.Rule:
    return brain_pb2.Rule(
        rule_id=str(r.id),
        employee_id=r.employee_id or "",
        text=r.text,
        status=r.status,
        proposal_id=str(r.proposal_id) if r.proposal_id else "",
        approved_by=r.approved_by,
        created_at=_ts(r.created_at),
        revoked_by=r.revoked_by or "",
        revoked_at=_ts(r.revoked_at),
    )


async def _person(tenant: common_pb2.TenantContext, context: grpc.aio.ServicerContext) -> str:
    """The acting person's id. Decisions about an employee's rules are for people only."""
    if tenant.actor.kind != common_pb2.ACTOR_KIND_HUMAN or not tenant.actor.id:
        await _abort(context, grpc.StatusCode.PERMISSION_DENIED, "only a person can do this")
    return tenant.actor.id


async def _uuid(value: str, name: str, context: grpc.aio.ServicerContext) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        await _abort(context, grpc.StatusCode.INVALID_ARGUMENT, f"{name} must be a UUID")


class BrainServicer(brain_pb2_grpc.BrainServiceServicer):
    def __init__(
        self, recall: Recall, inspector: Inspector, feedback: Feedback, rules: Rules
    ) -> None:
        self.recall_service = recall
        self.inspector = inspector
        self.feedback = feedback
        self.rules = rules

    # ---- Approvals and rules -----------------------------------------------------------------

    async def ListProposals(
        self, request: brain_pb2.ListProposalsRequest, context: grpc.aio.ServicerContext
    ) -> brain_pb2.ListProposalsResponse:
        tenant = await _tenant(request.tenant.tenant_id, context)
        if not request.employee_id:
            await _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "employee_id is required")
        if not set(request.statuses) <= {"pending", "approved", "rejected"}:
            await _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "unknown status")
        found = await self.rules.proposals(tenant, request.employee_id, list(request.statuses))
        return brain_pb2.ListProposalsResponse(proposals=[_proposal(p) for p in found])

    async def DecideProposal(
        self, request: brain_pb2.DecideProposalRequest, context: grpc.aio.ServicerContext
    ) -> brain_pb2.DecideProposalResponse:
        tenant = await _tenant(request.tenant.tenant_id, context)
        pid = await _uuid(request.proposal_id, "proposal_id", context)
        by = await _person(request.tenant, context)
        decide = self.rules.decide(tenant, pid, approve=request.approve, by=by, note=request.note)
        proposal, rule = await self._guard(context, decide)
        resp = brain_pb2.DecideProposalResponse(proposal=_proposal(proposal))
        if rule is not None:
            resp.rule.CopyFrom(_rule(rule))
        return resp

    async def ListRules(
        self, request: brain_pb2.ListRulesRequest, context: grpc.aio.ServicerContext
    ) -> brain_pb2.ListRulesResponse:
        tenant = await _tenant(request.tenant.tenant_id, context)
        if not request.employee_id:
            await _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "employee_id is required")
        found = await self.rules.rules(
            tenant, request.employee_id, include_revoked=request.include_revoked
        )
        return brain_pb2.ListRulesResponse(rules=[_rule(r) for r in found])

    async def RevokeRule(
        self, request: brain_pb2.RevokeRuleRequest, context: grpc.aio.ServicerContext
    ) -> brain_pb2.RevokeRuleResponse:
        tenant = await _tenant(request.tenant.tenant_id, context)
        rid = await _uuid(request.rule_id, "rule_id", context)
        by = await _person(request.tenant, context)
        rule = await self._guard(
            context, self.rules.revoke(tenant, rid, by=by, reason=request.reason)
        )
        return brain_pb2.RevokeRuleResponse(rule=_rule(rule))

    # ---- Feedback ----------------------------------------------------------------------------

    async def Feedback(
        self, request: brain_pb2.FeedbackRequest, context: grpc.aio.ServicerContext
    ) -> brain_pb2.FeedbackResponse:
        tenant = await _tenant(request.tenant.tenant_id, context)
        bad = grpc.StatusCode.INVALID_ARGUMENT
        kind = FEEDBACK_KINDS.get(request.kind)
        if kind is None:
            await _abort(context, bad, "kind is required")
        actor_kind = ACTOR_KINDS.get(request.tenant.actor.kind)
        if actor_kind is None or not request.tenant.actor.id:
            await _abort(context, bad, "tenant.actor (kind and id) is required")
        if not request.employee_id:
            await _abort(context, bad, "employee_id is required")
        key = request.idempotency_key.value.strip()
        if not key or len(key) > 200:
            await _abort(context, bad, "idempotency_key is required (at most 200 characters)")
        if request.rating not in (-1, 0, 1):
            await _abort(context, bad, "rating is 1 or -1")
        f = FeedbackIn(
            tenant_id=uuid.UUID(tenant),
            employee_id=request.employee_id,
            run_id=request.run_id or None,
            kind=kind,  # type: ignore[arg-type]
            text=request.text,
            rating=request.rating or None,  # type: ignore[arg-type]
            actor_id=request.tenant.actor.id,
            actor_kind=actor_kind,  # type: ignore[arg-type]
            idempotency_key=key,
        )
        try:
            given = await self.feedback.give(f)
        except Invalid as exc:
            await _abort(context, bad, str(exc))
        return brain_pb2.FeedbackResponse(
            feedback_id=str(given.feedback_id),
            proposal_id=str(given.proposal_id) if given.proposal_id else "",
            credited=given.credited,
        )

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
                candidate_proposal_id=request.candidate_proposal_id or None,
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
