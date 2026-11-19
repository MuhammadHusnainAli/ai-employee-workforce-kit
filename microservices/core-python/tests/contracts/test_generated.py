from wfk.common.v1 import common_pb2


def test_tenant_context_round_trips_through_protobuf() -> None:
    ctx = common_pb2.TenantContext(
        tenant_id="0193a7c4-0000-7000-8000-000000000001",
        cell_id="cell-local",
        actor=common_pb2.Actor(kind=common_pb2.ACTOR_KIND_SERVICE, id="agent-runtime"),
    )
    back = common_pb2.TenantContext.FromString(ctx.SerializeToString())
    assert back == ctx
    assert back.actor.kind == common_pb2.ACTOR_KIND_SERVICE


def test_unknown_outcome_is_its_own_error_class() -> None:
    detail = common_pb2.ErrorDetail(
        error_class=common_pb2.ERROR_CLASS_UNKNOWN_OUTCOME, code="connector_timeout"
    )
    assert detail.error_class != common_pb2.ERROR_CLASS_RETRYABLE
