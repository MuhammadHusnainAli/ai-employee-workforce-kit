//! service-template.
//!
//! Layout (see microservices/README.md):
//! - `api`: transport. gRPC services, HTTP routes, request validation.
//! - `domain`: business logic and invariants. No I/O.
//! - `adapters`: Postgres, NATS, other services.

pub mod adapters;
pub mod api;
pub mod domain;

use std::net::SocketAddr;
use std::time::Duration;

use axum::Router;
use futures::FutureExt;
use tokio::net::TcpListener;
use tonic_health::ServingStatus;
use wfk_platform::{Database, HealthRegistry, ServiceSettings, health_router};

pub const SERVICE_NAME: &str = "service-template";

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
    let pool = wfk_platform::db::lazy_pool(&settings.database_url, 10)?;
    Ok(Database { pool })
}

/// Serve HTTP and gRPC until `shutdown` resolves.
pub async fn serve(
    settings: ServiceSettings,
    http: TcpListener,
    grpc: TcpListener,
    shutdown: impl Future<Output = ()> + Send + 'static,
) -> anyhow::Result<()> {
    let db = database(&settings)?;
    let (reporter, health_service) = tonic_health::server::health_reporter();
    reporter
        .set_service_status("", ServingStatus::Serving)
        .await;

    let (stop_tx, stop_rx) = tokio::sync::watch::channel(false);
    tokio::spawn(async move {
        shutdown.await;
        let _ = stop_tx.send(true);
    });
    let wait = |mut rx: tokio::sync::watch::Receiver<bool>| async move {
        let _ = rx.wait_for(|stop| *stop).await;
    };

    tracing::info!(
        http = %http.local_addr()?,
        grpc = %grpc.local_addr()?,
        "started"
    );
    let http_server =
        axum::serve(http, http_router(&settings, db)).with_graceful_shutdown(wait(stop_rx.clone()));
    // Register api/ gRPC services here with `.add_service(...)`.
    let grpc_server = tonic::transport::Server::builder()
        .add_service(health_service)
        .serve_with_incoming_shutdown(
            tonic::transport::server::TcpIncoming::from(grpc),
            wait(stop_rx),
        );
    let (h, g) = tokio::join!(http_server, grpc_server);
    reporter
        .set_service_status("", ServingStatus::NotServing)
        .await;
    h?;
    g?;
    Ok(())
}

/// Bind both listeners from settings.
pub async fn bind(settings: &ServiceSettings) -> anyhow::Result<(TcpListener, TcpListener)> {
    let http: SocketAddr = format!("{}:{}", settings.http_host, settings.http_port).parse()?;
    let grpc: SocketAddr = format!("{}:{}", settings.http_host, settings.grpc_port).parse()?;
    Ok((
        TcpListener::bind(http).await?,
        TcpListener::bind(grpc).await?,
    ))
}
