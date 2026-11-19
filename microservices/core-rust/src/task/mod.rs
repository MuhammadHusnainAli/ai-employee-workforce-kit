//! task module (formerly task-service): the work inbox. Tasks, their run status, and the
//! commands that steer a run.
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

use crate::contracts::wfk::task_service::v1::task_service_server::TaskServiceServer;
use crate::platform::messaging::{run_inbox_consumer, run_outbox_relay};
use crate::platform::{Database, HealthRegistry, ServiceSettings, health_router};
use crate::task::adapters::{RunEvents, Tasks};
use crate::task::api::TaskApi;
use axum::Router;
use futures::FutureExt;
use sqlx::migrate::Migrator;
use tokio::net::TcpListener;
use tokio::sync::watch;
use tokio::task::JoinHandle;
use tonic_health::ServingStatus;

/// The name in this module's event sources (`//<cell>/task-service`), kept so consumers and
/// stored events see no change.
pub const SERVICE_NAME: &str = "task-service";
/// This module's own database.
pub const DATABASE: &str = "postgresql://wfk:wfk@127.0.0.1:7017/task_db";
/// This module's migrations, embedded at compile time.
pub static MIGRATOR: Migrator = sqlx::migrate!("./migrations/task");
/// The stream this module publishes to (task.*), and the one it reads run events from.
pub const STREAM: &str = "TASKS";
pub const RUNS_STREAM: &str = "RUNS";
pub const RUNS_CONSUMER: &str = "task-service-runs";

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

/// The module, ready to be served: its gRPC services and background loops.
pub struct Module {
    pub db: Database,
    tasks: Tasks,
}

impl Module {
    pub fn new(settings: &ServiceSettings) -> anyhow::Result<Self> {
        let db = database(settings)?;
        let tasks = Tasks::new(db.clone(), &settings.cell_id);
        Ok(Self { db, tasks })
    }

    /// Add TaskService to a set of gRPC routes.
    pub fn grpc(&self, routes: tonic::service::Routes) -> tonic::service::Routes {
        routes.add_service(TaskServiceServer::new(TaskApi(self.tasks.clone())))
    }

    /// Background loops: publish the outbox (TASKS), project agent-runtime's run events (RUNS).
    pub fn spawn_loops(
        &self,
        settings: &ServiceSettings,
        stop: watch::Receiver<bool>,
    ) -> Vec<JoinHandle<()>> {
        vec![
            tokio::spawn(run_outbox_relay(
                self.db.clone(),
                settings.nats_url.clone(),
                STREAM.into(),
                vec!["task.>".into()],
                stop.clone(),
            )),
            tokio::spawn(run_inbox_consumer(
                self.db.clone(),
                settings.nats_url.clone(),
                RUNS_STREAM.into(),
                RUNS_CONSUMER.into(),
                "run.>".into(),
                Arc::new(RunEvents(self.tasks.clone())),
                stop,
            )),
        ]
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
