use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use std::time::Duration;
use tokio::net::TcpListener;
use tonic_health::pb::HealthCheckRequest;
use tonic_health::pb::health_check_response::ServingStatus;
use tonic_health::pb::health_client::HealthClient;
use tower::ServiceExt;

// This service's own crate, kept in its own block so its position does not depend on the name.
use service_template::platform::ServiceSettings;
use service_template::{SERVICE_NAME, database, http_router, serve};

const UNREACHABLE: &str = "postgresql://wfk:wfk@127.0.0.1:1/nowhere";
const LOCAL: &str = "postgresql://wfk:wfk@127.0.0.1:7000/postgres";

fn settings(db_url: &str) -> ServiceSettings {
    ServiceSettings::from_vars([
        ("WFK_SERVICE_NAME".to_string(), SERVICE_NAME.to_string()),
        ("WFK_DATABASE_URL".to_string(), db_url.to_string()),
        ("WFK_READINESS_TIMEOUT_MS".to_string(), "500".to_string()),
    ])
    .unwrap()
}

async fn get(db_url: &str, path: &str) -> (StatusCode, serde_json::Value) {
    let s = settings(db_url);
    let resp = http_router(&s, database(&s).unwrap())
        .oneshot(Request::get(path).body(Body::empty()).unwrap())
        .await
        .unwrap();
    let status = resp.status();
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    (status, serde_json::from_slice(&bytes).unwrap())
}

#[tokio::test]
async fn liveness_does_not_need_the_database() {
    assert_eq!(get(UNREACHABLE, "/healthz").await.0, StatusCode::OK);
}

#[tokio::test]
async fn not_ready_without_the_database() {
    let (status, body) = get(UNREACHABLE, "/readyz").await;
    assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE);
    assert_ne!(body["checks"]["postgres"], "ok");
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn ready_with_the_database() {
    let (status, body) = get(LOCAL, "/readyz").await;
    assert_eq!(status, StatusCode::OK, "{body}");
}

#[tokio::test]
async fn serves_http_health_and_grpc_on_one_port_then_shuts_down() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let (stop, stopped) = tokio::sync::oneshot::channel::<()>();
    let server = tokio::spawn(serve(settings(UNREACHABLE), listener, async {
        let _ = stopped.await;
    }));

    // gRPC (HTTP/2) on the port...
    let channel = tonic::transport::Channel::from_shared(format!("http://{addr}"))
        .unwrap()
        .connect()
        .await
        .unwrap();
    let mut client = HealthClient::new(channel);
    let status = client
        .check(HealthCheckRequest {
            service: String::new(),
        })
        .await
        .unwrap()
        .into_inner()
        .status();
    assert_eq!(status, ServingStatus::Serving);

    // ...and HTTP/1.1 health on the same port.
    let mut tcp = tokio::net::TcpStream::connect(addr).await.unwrap();
    tokio::io::AsyncWriteExt::write_all(
        &mut tcp,
        b"GET /healthz HTTP/1.1
Host: t
Connection: close

",
    )
    .await
    .unwrap();
    let mut out = String::new();
    tokio::io::AsyncReadExt::read_to_string(&mut tcp, &mut out)
        .await
        .unwrap();
    assert!(out.starts_with("HTTP/1.1 200"), "{out}");

    drop(client);
    stop.send(()).unwrap();
    tokio::time::timeout(Duration::from_secs(5), server)
        .await
        .expect("shuts down")
        .unwrap()
        .unwrap();
}
