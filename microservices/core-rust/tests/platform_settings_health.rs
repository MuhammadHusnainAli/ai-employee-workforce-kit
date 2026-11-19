use axum::body::Body;
use axum::http::{Request, StatusCode};
use futures::FutureExt;
use http_body_util::BodyExt;
use std::time::Duration;
use tower::ServiceExt;

// This service's own crate, kept in its own block so its position does not depend on the name.
use core_rust::platform::{Environment, HealthRegistry, ServiceSettings, health_router};

fn vars(pairs: &[(&str, &str)]) -> Vec<(String, String)> {
    pairs
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect()
}

#[test]
fn reads_prefixed_variables_with_local_defaults() {
    let s = ServiceSettings::from_vars(vars(&[
        ("WFK_SERVICE_NAME", "task-service"),
        ("WFK_PORT", "9000"),
        ("WFK_ENVIRONMENT", "test"),
        ("UNRELATED", "ignored"),
    ]))
    .unwrap();
    assert_eq!(s.service_name, "task-service");
    assert_eq!(s.port, 9000);
    assert_eq!(s.environment, Environment::Test);
    assert_eq!(s.nats_url, "nats://127.0.0.1:7020");
    assert!(s.otlp_endpoint.is_empty());
}

#[test]
fn rejects_missing_or_invalid_values() {
    assert!(ServiceSettings::from_vars(vars(&[])).is_err());
    assert!(
        ServiceSettings::from_vars(vars(&[("WFK_SERVICE_NAME", "x"), ("WFK_PORT", "0")])).is_err()
    );
    assert!(
        ServiceSettings::from_vars(vars(&[
            ("WFK_SERVICE_NAME", "x"),
            ("WFK_ENVIRONMENT", "prod")
        ]))
        .is_err()
    );
}

async fn get(registry: HealthRegistry, path: &str) -> (StatusCode, serde_json::Value) {
    let resp = health_router(registry)
        .oneshot(Request::get(path).body(Body::empty()).unwrap())
        .await
        .unwrap();
    let status = resp.status();
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    (status, serde_json::from_slice(&bytes).unwrap())
}

#[tokio::test]
async fn ready_when_all_checks_pass() {
    let mut reg = HealthRegistry::new(Duration::from_secs(1));
    reg.add("db", || async { Ok(()) }.boxed());
    let (status, body) = get(reg, "/readyz").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["checks"]["db"], "ok");
}

#[tokio::test]
async fn not_ready_when_a_check_fails_or_times_out() {
    let mut reg = HealthRegistry::new(Duration::from_millis(50));
    reg.add("db", || {
        async { Err("connection refused".to_string()) }.boxed()
    });
    reg.add("nats", || {
        async {
            tokio::time::sleep(Duration::from_secs(5)).await;
            Ok(())
        }
        .boxed()
    });
    reg.add("cache", || async { Ok(()) }.boxed());
    let (status, body) = get(reg, "/readyz").await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(body["checks"]["db"], "error: connection refused");
    assert!(
        body["checks"]["nats"]
            .as_str()
            .unwrap()
            .starts_with("timeout")
    );
    assert_eq!(body["checks"]["cache"], "ok");
}

#[tokio::test]
async fn liveness_ignores_dependencies() {
    let mut reg = HealthRegistry::new(Duration::from_secs(1));
    reg.add("db", || async { Err("down".to_string()) }.boxed());
    let (status, _) = get(reg, "/healthz").await;
    assert_eq!(status, StatusCode::OK);
}

#[test]
fn telemetry_resource_names_the_service() {
    let s = ServiceSettings::from_vars(vars(&[
        ("WFK_SERVICE_NAME", "audit-service"),
        ("WFK_CELL_ID", "eu1"),
    ]))
    .unwrap();
    let r = core_rust::platform::telemetry::resource(&s);
    let get = |k: &str| {
        r.iter()
            .find(|(key, _)| key.as_str() == k)
            .map(|(_, v)| v.to_string())
    };
    assert_eq!(get("service.name").as_deref(), Some("audit-service"));
    assert_eq!(get("wfk.cell_id").as_deref(), Some("eu1"));
}

#[tokio::test]
async fn init_telemetry_installs_once() {
    let s = ServiceSettings::from_vars(vars(&[("WFK_SERVICE_NAME", "t")])).unwrap();
    let guard = core_rust::platform::init_telemetry(&s).expect("first init succeeds");
    tracing::info!(run_id = "r-1", "hello");
    assert!(
        core_rust::platform::init_telemetry(&s).is_err(),
        "a second global subscriber is refused"
    );
    drop(guard);
}
