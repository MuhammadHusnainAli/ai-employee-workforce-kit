//! core-rust: the Rust backend in one binary (ADR-0017). One port carries HTTP (health,
//! channel webhooks) and gRPC (TaskService, ChannelService, SandboxService).
//!
//! Modules, each with its own database, migrations, contracts and events:
//! - [`task`]: the work inbox (formerly task-service);
//! - [`channel`]: conversation channels to tasks and back (formerly channel-gateway);
//! - [`sandbox`]: untrusted code in hardened containers (formerly sandbox-runner).
//!
//! Modules never read each other's tables. The channel module reaches the task module over
//! TaskService on this service's own port, as it did across services.

pub mod channel;
pub mod contracts;
pub mod platform;
pub mod sandbox;
pub mod task;

use std::net::SocketAddr;
use std::time::Duration;

use crate::platform::{Database, HealthRegistry, ServiceSettings, health_router};
use axum::Router;
use futures::FutureExt;
use tokio::net::TcpListener;
use tokio::sync::watch;
use tonic_health::ServingStatus;

pub const SERVICE_NAME: &str = "core-rust";
/// This service's local port (deployments/README.md, port map).
pub const PORT: u16 = 7002;

/// core-rust's settings: the shared service settings plus each module's own database.
#[derive(Debug, Clone)]
pub struct Settings {
    pub service: ServiceSettings,
    pub task_database_url: String,
    pub channel_database_url: String,
    pub sandbox_database_url: String,
    /// Where the channel module reaches TaskService. Default: this service's own port.
    pub task_service_addr: String,
}

impl Settings {
    /// Read from `vars` (normally the environment): `WFK_TASK_DATABASE_URL`,
    /// `WFK_CHANNEL_DATABASE_URL`, `WFK_SANDBOX_DATABASE_URL`, `WFK_TASK_SERVICE_ADDR`, and the
    /// shared `WFK_*` settings. Defaults point at the local stack.
    pub fn from_vars<I>(vars: I) -> anyhow::Result<Self>
    where
        I: IntoIterator<Item = (String, String)>,
    {
        let mut vars: Vec<(String, String)> = vars.into_iter().collect();
        let get = |vars: &[(String, String)], key: &str| {
            vars.iter().find(|(k, _)| k == key).map(|(_, v)| v.clone())
        };
        for (key, value) in [
            ("WFK_SERVICE_NAME", SERVICE_NAME.to_string()),
            ("WFK_PORT", PORT.to_string()),
        ] {
            if get(&vars, key).is_none() {
                vars.push((key.into(), value));
            }
        }
        let service = ServiceSettings::from_vars(vars.clone())?;
        let or = |key: &str, default: &str| get(&vars, key).unwrap_or_else(|| default.into());
        Ok(Self {
            task_database_url: or("WFK_TASK_DATABASE_URL", task::DATABASE),
            channel_database_url: or("WFK_CHANNEL_DATABASE_URL", channel::DATABASE),
            sandbox_database_url: or("WFK_SANDBOX_DATABASE_URL", sandbox::DATABASE),
            task_service_addr: or(
                "WFK_TASK_SERVICE_ADDR",
                &format!("http://127.0.0.1:{}", service.port),
            ),
            service,
        })
    }

    pub fn from_env() -> anyhow::Result<Self> {
        Self::from_vars(std::env::vars())
    }

    /// The settings one module sees: the shared ones with its own database.
    pub fn module(&self, database_url: &str) -> ServiceSettings {
        ServiceSettings {
            database_url: database_url.into(),
            ..self.service.clone()
        }
    }
}

/// Every module, ready to be served.
pub struct Modules {
    pub task: task::Module,
    pub channel: channel::Module,
    pub sandbox: sandbox::Module,
}

impl Modules {
    pub fn new(settings: &Settings) -> anyhow::Result<Self> {
        Ok(Self {
            task: task::Module::new(&settings.module(&settings.task_database_url))?,
            channel: channel::Module::new(&settings.module(&settings.channel_database_url))?,
            sandbox: sandbox::Module::new(&settings.module(&settings.sandbox_database_url))?,
        })
    }

    fn databases(&self) -> [(&'static str, Database); 3] {
        [
            ("postgres:task", self.task.db.clone()),
            ("postgres:channel", self.channel.db.clone()),
            ("postgres:sandbox", self.sandbox.db.clone()),
        ]
    }
}

/// `/healthz` and `/readyz`; readiness checks every module's database.
pub fn http_router(settings: &Settings, modules: &Modules) -> Router {
    let timeout = Duration::from_millis(settings.service.readiness_timeout_ms);
    let mut registry = HealthRegistry::new(timeout);
    for (name, db) in modules.databases() {
        registry.add(name, move || {
            let db = db.clone();
            async move { db.ping().await.map_err(|e| e.to_string()) }.boxed()
        });
    }
    health_router(registry)
}

/// Health, every module's gRPC service and the channel webhooks on one router, so they share
/// one port. axum serves HTTP/1.1 and HTTP/2 (h2c) on the same listener; gRPC is HTTP/2.
pub fn app(settings: &Settings, modules: &Modules, grpc: tonic::service::Routes) -> Router {
    let grpc = modules
        .sandbox
        .grpc(modules.channel.grpc(modules.task.grpc(grpc)));
    http_router(settings, modules)
        .merge(grpc.into_axum_router())
        .merge(modules.channel.hooks())
}

/// Serve every module on one listener until `shutdown` resolves.
pub async fn serve(
    settings: Settings,
    listener: TcpListener,
    shutdown: impl Future<Output = ()> + Send + 'static,
) -> anyhow::Result<()> {
    let modules = Modules::new(&settings)?;
    let (reporter, health_service) = tonic_health::server::health_reporter();
    reporter
        .set_service_status("", ServingStatus::Serving)
        .await;
    let router = app(
        &settings,
        &modules,
        tonic::service::Routes::new(health_service),
    );

    let (stop_tx, stop) = watch::channel(false);
    let mut loops = Vec::new();
    let task_settings = settings.module(&settings.task_database_url);
    loops.extend(modules.task.spawn_loops(&task_settings, stop.clone()));
    let channel_settings = settings.module(&settings.channel_database_url);
    loops.extend(modules.channel.spawn_loops(
        &channel_settings,
        settings.task_service_addr.clone(),
        stop.clone(),
    ));
    let sandbox_settings = settings.module(&settings.sandbox_database_url);
    loops.extend(modules.sandbox.spawn_loops(&sandbox_settings, stop));

    tracing::info!(addr = %listener.local_addr()?, "started");
    let served = axum::serve(listener, router)
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
