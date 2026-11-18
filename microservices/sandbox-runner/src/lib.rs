//! sandbox-runner: untrusted code in hardened containers (gVisor pods in production).
//!
//! Layout (see microservices/README.md):
//! - `api`: transport. gRPC services, HTTP routes, request validation.
//! - `domain`: business logic and invariants. No I/O.
//! - `adapters`: Postgres, NATS, other services.

pub mod adapters;
pub mod api;
pub mod contracts;
pub mod domain;
pub mod platform;

use std::net::SocketAddr;
use std::time::Duration;

use std::sync::Arc;

use crate::adapters::{DockerRunner, Executions};
use crate::api::SandboxApi;
use crate::contracts::wfk::sandbox_runner::v1::sandbox_service_server::SandboxServiceServer;
use crate::platform::messaging::run_outbox_relay;
use crate::platform::{Database, HealthRegistry, ServiceSettings, health_router};
use axum::Router;
use futures::FutureExt;
use tokio::net::TcpListener;
use tokio::sync::{Semaphore, watch};
use tonic_health::ServingStatus;

pub const SERVICE_NAME: &str = "sandbox-runner";
/// This service's local port (deployments/README.md, port map) and database.
pub const PORT: u16 = 7006;
pub const DATABASE: &str = "postgresql://wfk:wfk@127.0.0.1:7017/sandbox_db";
/// The stream this service publishes to (usage.sandbox).
pub const STREAM: &str = "SANDBOX";

/// HTTP router: health endpoints now, REST routes from `api` later.
pub fn http_router(settings: &ServiceSettings, db: Database) -> Router {
    let mut registry = HealthRegistry::new(Duration::from_millis(settings.readiness_timeout_ms));
    registry.add("postgres", move || {
        let db = db.clone();
        async move { db.ping().await.map_err(|e| e.to_string()) }.boxed()
    });
    health_router(registry)
}

/// Connect lazily so the service starts while the database is down; `/readyz` reports it.
pub fn database(settings: &ServiceSettings) -> anyhow::Result<Database> {
    let pool = crate::platform::db::lazy_pool(&settings.database_url, 10)?;
    Ok(Database {
        pool,
        tenant_role: None,
    }
    .with_tenant_role(&settings.db_tenant_role))
}

/// HTTP health routes and gRPC services on one router, so they share one port. axum serves
/// HTTP/1.1 and HTTP/2 (h2c) on the same listener; gRPC is HTTP/2.
pub fn app(settings: &ServiceSettings, db: Database, grpc: tonic::service::Routes) -> Router {
    http_router(settings, db).merge(grpc.into_axum_router())
}

/// Serve health and gRPC on one listener until `shutdown` resolves.
pub async fn serve(
    settings: ServiceSettings,
    listener: TcpListener,
    shutdown: impl Future<Output = ()> + Send + 'static,
) -> anyhow::Result<()> {
    let db = database(&settings)?;
    let runner = DockerRunner::connect(std::env::var("WFK_SANDBOX_RUNTIME").ok())?;
    let slots = std::env::var("WFK_SANDBOX_SLOTS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(4);
    let (reporter, health_service) = tonic_health::server::health_reporter();
    reporter
        .set_service_status("", ServingStatus::Serving)
        .await;
    let grpc = tonic::service::Routes::new(health_service).add_service(SandboxServiceServer::new(
        SandboxApi {
            runner,
            executions: Executions::new(db.clone(), &settings.cell_id),
            slots: Arc::new(Semaphore::new(slots)),
        },
    ));

    // Publish usage.sandbox from the outbox.
    let (stop_tx, stop) = watch::channel(false);
    let relay = tokio::spawn(run_outbox_relay(
        db.clone(),
        settings.nats_url.clone(),
        STREAM.into(),
        vec!["usage.sandbox".into()],
        stop,
    ));

    tracing::info!(addr = %listener.local_addr()?, "started");
    let served = axum::serve(listener, app(&settings, db, grpc))
        .with_graceful_shutdown(shutdown)
        .await;
    reporter
        .set_service_status("", ServingStatus::NotServing)
        .await;
    let _ = stop_tx.send(true);
    let _ = relay.await;
    Ok(served?)
}

/// Bind the service's one port from settings.
pub async fn bind(settings: &ServiceSettings) -> anyhow::Result<TcpListener> {
    let addr: SocketAddr = format!("{}:{}", settings.host, settings.port).parse()?;
    Ok(TcpListener::bind(addr).await?)
}
