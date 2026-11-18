//! channel-gateway: conversation channels (Slack, Teams, Google Chat, email, webhooks) to tasks and back.
//!
//! Layout (see microservices/README.md):
//! - `api`: transport. gRPC services, HTTP routes, request validation.
//! - `domain`: business logic and invariants. No I/O.
//! - `adapters`: Postgres, NATS, other services.

pub mod adapters;
pub mod api;
pub mod contracts;
pub mod domain;
pub mod flows;
pub mod platform;

use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use crate::adapters::{Channels, GOOGLE_CHAT_JWKS, GoogleTokens};
use crate::api::{ChannelApi, HookState};
use crate::contracts::wfk::channel_gateway::v1::channel_service_server::ChannelServiceServer;
use crate::platform::messaging::{run_inbox_consumer, run_outbox_relay};
use crate::platform::{Database, HealthRegistry, ServiceSettings, health_router};
use axum::Router;
use futures::FutureExt;
use tokio::net::TcpListener;
use tokio::sync::watch;
use tonic_health::ServingStatus;

pub const SERVICE_NAME: &str = "channel-gateway";
/// This service's local port (deployments/README.md, port map) and database.
pub const PORT: u16 = 7013;
pub const DATABASE: &str = "postgresql://wfk:wfk@127.0.0.1:7000/channel_db";
/// The stream this service publishes to (channel.*).
pub const STREAM: &str = "CHANNELS";
/// agent-runtime's run events: answers go back to the thread a task came from.
pub const RUNS_STREAM: &str = "RUNS";
pub const RUNS_CONSUMER: &str = "channel-gateway-runs";

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
    let channels = Channels::new(db.clone(), &settings.cell_id);
    let (reporter, health_service) = tonic_health::server::health_reporter();
    reporter
        .set_service_status("", ServingStatus::Serving)
        .await;
    let grpc = tonic::service::Routes::new(health_service)
        .add_service(ChannelServiceServer::new(ChannelApi(channels.clone())));
    let hooks = api::hooks(HookState {
        channels: channels.clone(),
        google: GoogleTokens::new(
            std::env::var("WFK_GOOGLE_CHAT_JWKS_URL").unwrap_or_else(|_| GOOGLE_CHAT_JWKS.into()),
        ),
    });

    // Background loops: publish channel.*, forward messages to task-service, turn run events
    // into replies, and send the replies.
    let (stop_tx, stop) = watch::channel(false);
    let task_service =
        std::env::var("WFK_TASK_SERVICE_ADDR").unwrap_or_else(|_| "http://127.0.0.1:7016".into());
    let loops = [
        tokio::spawn(run_outbox_relay(
            db.clone(),
            settings.nats_url.clone(),
            STREAM.into(),
            vec!["channel.>".into()],
            stop.clone(),
        )),
        tokio::spawn(flows::run_forwarder(
            channels.clone(),
            task_service,
            stop.clone(),
        )),
        tokio::spawn(flows::run_sender(channels.clone(), stop.clone())),
        tokio::spawn(run_inbox_consumer(
            db.clone(),
            settings.nats_url.clone(),
            RUNS_STREAM.into(),
            RUNS_CONSUMER.into(),
            "run.>".into(),
            Arc::new(flows::RunAnswers(channels.clone())),
            stop,
        )),
    ];

    tracing::info!(addr = %listener.local_addr()?, "started");
    let served = axum::serve(listener, app(&settings, db, grpc).merge(hooks))
        .with_graceful_shutdown(shutdown)
        .await;
    reporter
        .set_service_status("", ServingStatus::NotServing)
        .await;
    let _ = stop_tx.send(true);
    for handle in loops {
        let _ = handle.await;
    }
    Ok(served?)
}

/// Bind the service's one port from settings.
pub async fn bind(settings: &ServiceSettings) -> anyhow::Result<TcpListener> {
    let addr: SocketAddr = format!("{}:{}", settings.host, settings.port).parse()?;
    Ok(TcpListener::bind(addr).await?)
}
