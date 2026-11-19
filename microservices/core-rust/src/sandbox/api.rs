//! SandboxService over gRPC (contracts/proto/wfk/sandbox_runner/v1/sandbox.proto).

use std::sync::Arc;

use tokio::sync::Semaphore;
use tonic::{Request, Response, Status};
use uuid::Uuid;

use crate::contracts::wfk::sandbox_runner::v1 as pb;
use crate::contracts::wfk::sandbox_runner::v1::sandbox_service_server::SandboxService;
use crate::sandbox::adapters::{DockerRunner, Executions, Outcome};
use crate::sandbox::domain::{Limits, Spec};

pub struct SandboxApi {
    pub runner: DockerRunner,
    pub executions: Executions,
    /// How many runs may execute at once on this replica; the rest wait their turn.
    pub slots: Arc<Semaphore>,
}

fn bad(msg: impl Into<String>) -> Status {
    Status::invalid_argument(msg.into())
}

fn response(id: Uuid, o: Outcome, replayed: bool) -> pb::RunSandboxResponse {
    pb::RunSandboxResponse {
        execution_id: id.to_string(),
        exit_code: o.exit_code,
        stdout: o.stdout,
        stderr: o.stderr,
        timed_out: o.timed_out,
        oom_killed: o.oom_killed,
        output_truncated: o.output_truncated,
        duration_ms: o.duration_ms,
        replayed,
    }
}

#[tonic::async_trait]
impl SandboxService for SandboxApi {
    async fn run_sandbox(
        &self,
        req: Request<pb::RunSandboxRequest>,
    ) -> Result<Response<pb::RunSandboxResponse>, Status> {
        let r = req.into_inner();
        let tenant = r
            .tenant
            .as_ref()
            .and_then(|t| Uuid::parse_str(&t.tenant_id).ok())
            .ok_or_else(|| bad("tenant.tenant_id must be a UUID"))?;
        let key = r
            .idempotency_key
            .map(|k| k.value.trim().to_string())
            .filter(|k| !k.is_empty() && k.len() <= 200)
            .ok_or_else(|| bad("idempotency_key is required (at most 200 characters)"))?;
        let l = r.limits.unwrap_or_default();
        let limits =
            Limits::resolve(l.timeout_ms, l.memory_mb, l.cpu_milli, l.pids).map_err(bad)?;
        let spec = Spec::new(&r.image, r.code, r.stdin, limits).map_err(bad)?;

        let id = Uuid::new_v5(&tenant, format!("sandbox/{key}").as_bytes());
        let stored = self.executions.get(tenant, id).await.map_err(|e| {
            tracing::error!(error = %e, "executions store");
            Status::unavailable("store unavailable")
        })?;
        if let Some(o) = stored {
            return Ok(Response::new(response(id, o, true)));
        }

        let _slot = self
            .slots
            .acquire()
            .await
            .map_err(|_| Status::unavailable("shutting down"))?;
        let outcome = self
            .runner
            .run(&spec, &format!("wfk-sandbox-{id}"))
            .await
            .map_err(|e| {
                let msg = e.to_string();
                if msg.contains("Conflict") || msg.contains("409") {
                    return Status::aborted("this execution is already running");
                }
                tracing::error!(error = %e, "sandbox run failed");
                Status::unavailable("the container runtime is unavailable")
            })?;
        self.executions
            .record(tenant, id, &r.run_id, spec.image.name, &outcome)
            .await
            .map_err(|e| {
                tracing::error!(error = %e, "recording the execution failed");
                Status::unavailable("store unavailable")
            })?;
        Ok(Response::new(response(id, outcome, false)))
    }
}
