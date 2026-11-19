//! The composed binary: one port carries every module's gRPC service, the webhooks and health;
//! each module keeps its own database; readiness checks all of them.

use std::time::Duration;
use tokio::net::TcpListener;
use tonic_health::pb::HealthCheckRequest;
use tonic_health::pb::health_check_response::ServingStatus;
use tonic_health::pb::health_client::HealthClient;

// This service's own crate, kept in its own block so its position does not depend on the name.
use core_rust::contracts::wfk::channel_gateway::v1::ListBindingsRequest;
use core_rust::contracts::wfk::channel_gateway::v1::channel_service_client::ChannelServiceClient;
use core_rust::contracts::wfk::sandbox_runner::v1::RunSandboxRequest;
use core_rust::contracts::wfk::sandbox_runner::v1::sandbox_service_client::SandboxServiceClient;
use core_rust::contracts::wfk::task_service::v1::GetTaskRequest;
use core_rust::contracts::wfk::task_service::v1::task_service_client::TaskServiceClient;
use core_rust::platform::db::OUTBOX_INBOX_SQL;
use core_rust::{PORT, Settings, serve};

const UNREACHABLE: &str = "postgresql://wfk:wfk@127.0.0.1:1/nowhere";

fn vars(pairs: &[(&str, &str)]) -> Vec<(String, String)> {
    pairs
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect()
}

fn offline(port: u16) -> Settings {
    Settings::from_vars(vars(&[
        ("WFK_PORT", &port.to_string()),
        ("WFK_HOST", "127.0.0.1"),
        ("WFK_TASK_DATABASE_URL", UNREACHABLE),
        ("WFK_CHANNEL_DATABASE_URL", UNREACHABLE),
        ("WFK_SANDBOX_DATABASE_URL", UNREACHABLE),
        ("WFK_READINESS_TIMEOUT_MS", "500"),
        ("WFK_NATS_URL", "nats://127.0.0.1:1"),
    ]))
    .unwrap()
}

#[test]
fn defaults_give_each_module_its_own_database_and_one_port() {
    let s = Settings::from_vars(Vec::new()).unwrap();
    assert_eq!(s.service.port, PORT);
    assert_eq!(s.service.service_name, "core-rust");
    let names: Vec<&str> = [
        &s.task_database_url,
        &s.channel_database_url,
        &s.sandbox_database_url,
    ]
    .iter()
    .map(|u| u.rsplit_once('/').unwrap().1)
    .collect();
    assert_eq!(names, ["task_db", "channel_db", "sandbox_db"]);
    // The channel module reaches the task module on this service's own port.
    assert_eq!(s.task_service_addr, format!("http://127.0.0.1:{PORT}"));
    assert_eq!(
        s.module(&s.channel_database_url).database_url,
        s.channel_database_url
    );
}

#[test]
fn the_environment_overrides_the_defaults() {
    let s = Settings::from_vars(vars(&[
        ("WFK_PORT", "7099"),
        ("WFK_TASK_DATABASE_URL", "postgresql://x/tasks"),
        ("WFK_TASK_SERVICE_ADDR", "http://elsewhere:1"),
    ]))
    .unwrap();
    assert_eq!(s.service.port, 7099);
    assert_eq!(s.task_database_url, "postgresql://x/tasks");
    assert_eq!(s.task_service_addr, "http://elsewhere:1");
}

#[test]
fn every_module_starts_from_the_platform_schema() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("migrations");
    for module in ["task", "channel", "sandbox"] {
        let path = root.join(module).join("0001_platform_outbox_inbox.up.sql");
        let sql = std::fs::read_to_string(&path).unwrap();
        assert_eq!(sql, OUTBOX_INBOX_SQL, "{}", path.display());
    }
}

/// gRPC status of a call: anything but UNIMPLEMENTED means a service answers on the port.
fn served(result: Result<impl Sized, tonic::Status>) -> bool {
    !matches!(result, Err(s) if s.code() == tonic::Code::Unimplemented)
}

#[tokio::test]
async fn one_port_serves_every_module_then_shuts_down() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let (stop_tx, stop_rx) = tokio::sync::oneshot::channel::<()>();
    let server = tokio::spawn(serve(offline(addr.port()), listener, async {
        let _ = stop_rx.await;
    }));

    let url = format!("http://{addr}");
    let channel = tonic::transport::Endpoint::from_shared(url.clone())
        .unwrap()
        .connect_timeout(Duration::from_secs(2))
        .timeout(Duration::from_secs(3))
        .connect_lazy();

    // HTTP health on the same port: alive, and not ready without the databases.
    let http = reqwest::Client::new();
    let live = http.get(format!("{url}/healthz")).send().await.unwrap();
    assert_eq!(live.status(), 200);
    let ready = http.get(format!("{url}/readyz")).send().await.unwrap();
    assert_eq!(ready.status(), 503);
    let body: serde_json::Value = ready.json().await.unwrap();
    for check in ["postgres:task", "postgres:channel", "postgres:sandbox"] {
        assert_ne!(body["checks"][check], "ok", "{check}: {body}");
    }

    // gRPC health, and each module's service.
    let mut health = HealthClient::new(channel.clone());
    let status = health
        .check(HealthCheckRequest {
            service: String::new(),
        })
        .await
        .unwrap()
        .into_inner()
        .status;
    assert_eq!(status, ServingStatus::Serving as i32);
    let mut tasks = TaskServiceClient::new(channel.clone());
    assert!(served(tasks.get_task(GetTaskRequest::default()).await));
    let mut channels = ChannelServiceClient::new(channel.clone());
    assert!(served(
        channels.list_bindings(ListBindingsRequest::default()).await
    ));
    let mut sandbox = SandboxServiceClient::new(channel.clone());
    assert!(served(
        sandbox.run_sandbox(RunSandboxRequest::default()).await
    ));

    // The webhooks are on the same port too: the channel module itself answers an unknown
    // binding (a missing route would give an empty 404).
    let hook = http
        .post(format!("{url}/hooks/webhook/not-a-uuid"))
        .body("{}")
        .send()
        .await
        .unwrap();
    assert_eq!(hook.status(), 404);
    assert!(hook.text().await.unwrap().contains("no such hook"));

    stop_tx.send(()).unwrap();
    tokio::time::timeout(Duration::from_secs(10), server)
        .await
        .expect("shuts down")
        .unwrap()
        .unwrap();
}
