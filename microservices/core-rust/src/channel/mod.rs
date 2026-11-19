//! channel module (formerly channel-gateway): conversation channels (Slack, Teams, Google Chat,
//! email, webhooks) to tasks and back.
//!
//! Layout:
//! - `api`: transport. gRPC services, HTTP routes, request validation.
//! - `domain`: business logic and invariants. No I/O.
//! - `adapters`: Postgres, NATS, other services.

pub mod adapters;
pub mod api;
pub mod domain;
pub mod flows;

use std::sync::Arc;
use std::time::Duration;

use crate::channel::adapters::{Channels, GOOGLE_CHAT_JWKS, GoogleTokens};
use crate::channel::api::{ChannelApi, HookState};
use crate::contracts::wfk::channel_gateway::v1::channel_service_server::ChannelServiceServer;
use crate::platform::messaging::{run_inbox_consumer, run_outbox_relay};
use crate::platform::{Database, HealthRegistry, ServiceSettings, health_router};
use axum::Router;
use futures::FutureExt;
use sqlx::migrate::Migrator;
use tokio::net::TcpListener;
use tokio::sync::watch;
use tokio::task::JoinHandle;
use tonic_health::ServingStatus;

/// The name in this module's event sources (`//<cell>/channel-gateway`), kept so consumers
/// and stored events see no change.
pub const SERVICE_NAME: &str = "channel-gateway";
/// This module's own database.
pub const DATABASE: &str = "postgresql://wfk:wfk@127.0.0.1:7017/channel_db";
/// This module's migrations, embedded at compile time.
pub static MIGRATOR: Migrator = sqlx::migrate!("./migrations/channel");
/// The stream this module publishes to (channel.*).
pub const STREAM: &str = "CHANNELS";
/// agent-runtime's run events: answers go back to the thread a task came from.
pub const RUNS_STREAM: &str = "RUNS";
pub const RUNS_CONSUMER: &str = "channel-gateway-runs";

/// HTTP router with this module's health endpoints (the module on its own, in tests).
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

/// The module, ready to be served: its gRPC service, webhook routes and background loops.
pub struct Module {
    pub db: Database,
    channels: Channels,
}

impl Module {
    pub fn new(settings: &ServiceSettings) -> anyhow::Result<Self> {
        let db = database(settings)?;
        let channels = Channels::new(db.clone(), &settings.cell_id);
        Ok(Self { db, channels })
    }

    /// Add ChannelService to a set of gRPC routes.
    pub fn grpc(&self, routes: tonic::service::Routes) -> tonic::service::Routes {
        routes.add_service(ChannelServiceServer::new(ChannelApi(self.channels.clone())))
    }

    /// The webhook routes providers post to: `/hooks/{kind}/{binding_id}`.
    pub fn hooks(&self) -> Router {
        api::hooks(HookState {
            channels: self.channels.clone(),
            google: GoogleTokens::new(
                std::env::var("WFK_GOOGLE_CHAT_JWKS_URL")
                    .unwrap_or_else(|_| GOOGLE_CHAT_JWKS.into()),
            ),
        })
    }

    /// Background loops: publish channel.*, forward messages to the task module at
    /// `task_service` (a gRPC address), turn run events into replies, and send the replies.
    pub fn spawn_loops(
        &self,
        settings: &ServiceSettings,
        task_service: String,
        stop: watch::Receiver<bool>,
    ) -> Vec<JoinHandle<()>> {
        vec![
            tokio::spawn(run_outbox_relay(
                self.db.clone(),
                settings.nats_url.clone(),
                STREAM.into(),
                vec!["channel.>".into()],
                stop.clone(),
            )),
            tokio::spawn(flows::run_forwarder(
                self.channels.clone(),
                task_service,
                stop.clone(),
            )),
            tokio::spawn(flows::run_sender(self.channels.clone(), stop.clone())),
            tokio::spawn(run_inbox_consumer(
                self.db.clone(),
                settings.nats_url.clone(),
                RUNS_STREAM.into(),
                RUNS_CONSUMER.into(),
                "run.>".into(),
                Arc::new(flows::RunAnswers(self.channels.clone())),
                stop,
            )),
        ]
    }
}

/// Serve this module alone until `shutdown` resolves. The task module is reached at
/// `WFK_TASK_SERVICE_ADDR` (default: this service's own port).
pub async fn serve(
    settings: ServiceSettings,
    listener: TcpListener,
    shutdown: impl Future<Output = ()> + Send + 'static,
) -> anyhow::Result<()> {
    let module = Module::new(&settings)?;
    let (reporter, health_service) = tonic_health::server::health_reporter();
    reporter
        .set_service_status("", ServingStatus::Serving)
        .await;
    let grpc = module.grpc(tonic::service::Routes::new(health_service));
    let task_service = std::env::var("WFK_TASK_SERVICE_ADDR")
        .unwrap_or_else(|_| format!("http://127.0.0.1:{}", settings.port));
    let (stop_tx, stop) = watch::channel(false);
    let loops = module.spawn_loops(&settings, task_service, stop);

    tracing::info!(addr = %listener.local_addr()?, "started");
    let app = http_router(&settings, module.db.clone())
        .merge(grpc.into_axum_router())
        .merge(module.hooks());
    let served = axum::serve(listener, app)
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
