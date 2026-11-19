//! sandbox module (formerly sandbox-runner): untrusted code in hardened containers (gVisor
//! pods in production).
//!
//! Layout:
//! - `api`: transport. gRPC services, HTTP routes, request validation.
//! - `domain`: business logic and invariants. No I/O.
//! - `adapters`: Postgres, NATS, other services.

pub mod adapters;
pub mod api;
pub mod domain;

use std::sync::Arc;
use std::time::Duration;

use crate::contracts::wfk::sandbox_runner::v1::sandbox_service_server::SandboxServiceServer;
use crate::platform::messaging::run_outbox_relay;
use crate::platform::{Database, HealthRegistry, ServiceSettings, health_router};
use crate::sandbox::adapters::{DockerRunner, Executions};
use crate::sandbox::api::SandboxApi;
use axum::Router;
use futures::FutureExt;
use sqlx::migrate::Migrator;
use tokio::net::TcpListener;
use tokio::sync::{Semaphore, watch};
use tokio::task::JoinHandle;
use tonic_health::ServingStatus;

/// The name in this module's event sources (`//<cell>/sandbox-runner`), kept so consumers and
/// stored events see no change.
pub const SERVICE_NAME: &str = "sandbox-runner";
/// This module's own database.
pub const DATABASE: &str = "postgresql://wfk:wfk@127.0.0.1:7017/sandbox_db";
/// This module's migrations, embedded at compile time.
pub static MIGRATOR: Migrator = sqlx::migrate!("./migrations/sandbox");
/// The stream this module publishes to (usage.sandbox).
pub const STREAM: &str = "SANDBOX";

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

/// The module, ready to be served: its gRPC service and the outbox relay.
pub struct Module {
    pub db: Database,
    runner: DockerRunner,
    executions: Executions,
    slots: Arc<Semaphore>,
}

impl Module {
    /// Docker is reached lazily: the module starts while Docker is down, and a run then fails.
    pub fn new(settings: &ServiceSettings) -> anyhow::Result<Self> {
        let db = database(settings)?;
        let runner = DockerRunner::connect(std::env::var("WFK_SANDBOX_RUNTIME").ok())?;
        let slots = std::env::var("WFK_SANDBOX_SLOTS")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(4);
        Ok(Self {
            executions: Executions::new(db.clone(), &settings.cell_id),
            db,
            runner,
            slots: Arc::new(Semaphore::new(slots)),
        })
    }

    /// Add SandboxService to a set of gRPC routes.
    pub fn grpc(&self, routes: tonic::service::Routes) -> tonic::service::Routes {
        routes.add_service(SandboxServiceServer::new(SandboxApi {
            runner: self.runner.clone(),
            executions: self.executions.clone(),
            slots: self.slots.clone(),
        }))
    }

    /// Publish usage.sandbox from the outbox.
    pub fn spawn_loops(
        &self,
        settings: &ServiceSettings,
        stop: watch::Receiver<bool>,
    ) -> Vec<JoinHandle<()>> {
        vec![tokio::spawn(run_outbox_relay(
            self.db.clone(),
            settings.nats_url.clone(),
            STREAM.into(),
            vec!["usage.sandbox".into()],
            stop,
        ))]
    }
}

/// Serve this module alone (health and gRPC on one listener) until `shutdown` resolves.
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
    let (stop_tx, stop) = watch::channel(false);
    let loops = module.spawn_loops(&settings, stop);

    tracing::info!(addr = %listener.local_addr()?, "started");
    let app = http_router(&settings, module.db.clone()).merge(grpc.into_axum_router());
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
