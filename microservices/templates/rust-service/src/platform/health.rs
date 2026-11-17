//! Liveness (`/healthz`) and readiness (`/readyz`) endpoints.
//!
//! Liveness only says the process is serving requests. It never checks dependencies, so a
//! database outage does not make Kubernetes restart every pod. Readiness runs the registered
//! checks concurrently, each with a deadline, and returns 503 if any fails.

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::get;
use axum::{Json, Router};
use futures::future::{BoxFuture, join_all};
use serde_json::json;

/// A readiness check: resolves to `Ok(())` when healthy.
pub type Check = Arc<dyn Fn() -> BoxFuture<'static, Result<(), String>> + Send + Sync>;

#[derive(Clone)]
pub struct HealthRegistry {
    timeout: Duration,
    checks: Vec<(String, Check)>,
}

impl HealthRegistry {
    pub fn new(timeout: Duration) -> Self {
        Self {
            timeout,
            checks: Vec::new(),
        }
    }

    pub fn add<F>(&mut self, name: impl Into<String>, check: F)
    where
        F: Fn() -> BoxFuture<'static, Result<(), String>> + Send + Sync + 'static,
    {
        self.checks.push((name.into(), Arc::new(check)));
    }

    /// Run every check. Returns overall readiness and the status of each check.
    pub async fn readiness(&self) -> (bool, BTreeMap<String, String>) {
        let runs = self.checks.iter().map(|(name, check)| {
            let fut = check();
            let timeout = self.timeout;
            async move {
                let status = match tokio::time::timeout(timeout, fut).await {
                    Ok(Ok(())) => "ok".to_string(),
                    Ok(Err(e)) => {
                        tracing::warn!(check = %name, error = %e, "readiness check failed");
                        format!("error: {e}")
                    }
                    Err(_) => format!("timeout after {}ms", timeout.as_millis()),
                };
                (name.clone(), status)
            }
        });
        let status: BTreeMap<_, _> = join_all(runs).await.into_iter().collect();
        let ready = status.values().all(|v| v == "ok");
        (ready, status)
    }
}

async fn healthz() -> impl IntoResponse {
    Json(json!({"status": "ok"}))
}

async fn readyz(State(reg): State<Arc<HealthRegistry>>) -> impl IntoResponse {
    let (ready, checks) = reg.readiness().await;
    let code = if ready {
        StatusCode::OK
    } else {
        StatusCode::SERVICE_UNAVAILABLE
    };
    let body = json!({"status": if ready { "ok" } else { "unavailable" }, "checks": checks});
    (code, Json(body))
}

/// Router with `/healthz` and `/readyz`. Merge it into the service's HTTP router.
pub fn health_router(registry: HealthRegistry) -> Router {
    Router::new()
        .route("/healthz", get(healthz))
        .route("/readyz", get(readyz))
        .with_state(Arc::new(registry))
}
