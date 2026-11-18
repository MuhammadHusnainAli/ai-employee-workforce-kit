use prost::Message;

// This service's own crate, kept in its own block so its position does not depend on the name.
use channel_gateway::contracts::wfk::common::v1::{ErrorClass, ErrorDetail, TenantContext};

#[test]
fn tenant_context_round_trips_through_protobuf() {
    let ctx = TenantContext {
        tenant_id: "0193a7c4-0000-7000-8000-000000000001".into(),
        cell_id: "cell-local".into(),
        employee_id: String::new(),
        actor: None,
    };
    let bytes = ctx.encode_to_vec();
    let back = TenantContext::decode(bytes.as_slice()).expect("decode");
    assert_eq!(back, ctx);
}

#[test]
fn error_class_unknown_outcome_is_distinct_from_retryable() {
    let d = ErrorDetail {
        error_class: ErrorClass::UnknownOutcome as i32,
        code: "connector_timeout".into(),
        message: "no response".into(),
        retry_after_ms: 0,
    };
    assert_eq!(d.error_class(), ErrorClass::UnknownOutcome);
    assert_ne!(d.error_class(), ErrorClass::Retryable);
}
