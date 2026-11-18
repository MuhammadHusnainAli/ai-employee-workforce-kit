//! The container backend (Docker locally; the gVisor pod spec is `domain::Spec::pod`) and the
//! executions store that makes runs idempotent and emits `usage.sandbox`.

use std::collections::HashMap;
use std::time::{Duration, Instant};

use bollard::Docker;
use bollard::container::LogOutput;
use bollard::errors::Error as DockerError;
use bollard::models::{ContainerCreateBody, HostConfig};
use bollard::query_parameters::{
    CreateContainerOptionsBuilder, CreateImageOptionsBuilder, KillContainerOptionsBuilder,
    LogsOptionsBuilder, RemoveContainerOptionsBuilder, WaitContainerOptions,
};
use futures::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::json;
use uuid::Uuid;

use crate::domain::{self, MAX_OUTPUT_BYTES, SANDBOX_USER, Spec, TMP_SIZE_MB};
use crate::platform::{CloudEvent, Database, enqueue};

pub const SOURCE_SERVICE: &str = "sandbox-runner";

/// What a run did.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Outcome {
    pub exit_code: i64,
    pub stdout: String,
    pub stderr: String,
    pub timed_out: bool,
    pub oom_killed: bool,
    pub output_truncated: bool,
    pub duration_ms: u64,
}

/// Runs specs in hardened containers through the Docker API.
#[derive(Clone)]
pub struct DockerRunner {
    docker: Docker,
    /// The OCI runtime: `runsc` (gVisor) where it is installed, else Docker's default.
    runtime: Option<String>,
}

impl DockerRunner {
    pub fn connect(runtime: Option<String>) -> anyhow::Result<Self> {
        Ok(Self {
            docker: Docker::connect_with_local_defaults()?,
            runtime: runtime.filter(|r| !r.is_empty()),
        })
    }

    pub async fn ping(&self) -> anyhow::Result<()> {
        self.docker.ping().await?;
        Ok(())
    }

    /// Pull the image unless it is there already.
    pub async fn ensure_image(&self, reference: &str) -> anyhow::Result<()> {
        if self.docker.inspect_image(reference).await.is_ok() {
            return Ok(());
        }
        let opts = CreateImageOptionsBuilder::default()
            .from_image(reference)
            .build();
        let mut pull = self.docker.create_image(Some(opts), None, None);
        while let Some(step) = pull.next().await {
            step?;
        }
        Ok(())
    }

    /// The container's settings: everything the sandbox promises is here.
    pub fn config(&self, spec: &Spec) -> ContainerCreateBody {
        let l = spec.limits;
        let memory = i64::try_from(l.memory_mb * 1024 * 1024).unwrap_or(i64::MAX);
        let mut labels = HashMap::new();
        labels.insert("wfk.io/sandbox".to_string(), "true".to_string());
        ContainerCreateBody {
            image: Some(spec.image.reference.to_string()),
            cmd: Some(spec.command()),
            env: Some(
                spec.env()
                    .into_iter()
                    .map(|(k, v)| format!("{k}={v}"))
                    .collect(),
            ),
            user: Some(SANDBOX_USER.to_string()),
            working_dir: Some("/tmp".to_string()),
            network_disabled: Some(true),
            labels: Some(labels),
            host_config: Some(HostConfig {
                network_mode: Some("none".to_string()),
                readonly_rootfs: Some(true),
                cap_drop: Some(vec!["ALL".to_string()]),
                security_opt: Some(vec!["no-new-privileges:true".to_string()]),
                privileged: Some(false),
                memory: Some(memory),
                memory_swap: Some(memory), // no swap: memory is a hard limit
                nano_cpus: Some(i64::try_from(l.cpu_milli * 1_000_000).unwrap_or(i64::MAX)),
                pids_limit: Some(l.pids),
                tmpfs: Some(HashMap::from([(
                    "/tmp".to_string(),
                    format!("rw,noexec,nosuid,nodev,size={TMP_SIZE_MB}m,mode=1777"),
                )])),
                init: Some(true),
                runtime: self.runtime.clone(),
                ..Default::default()
            }),
            ..Default::default()
        }
    }

    /// Run one spec to the end (or the deadline), collect its output, remove the container.
    pub async fn run(&self, spec: &Spec, name: &str) -> anyhow::Result<Outcome> {
        self.ensure_image(spec.image.reference).await?;
        let opts = CreateContainerOptionsBuilder::default().name(name).build();
        self.docker
            .create_container(Some(opts), self.config(spec))
            .await?;
        let result = self.run_created(spec, name).await;
        let rm = RemoveContainerOptionsBuilder::default().force(true).build();
        if let Err(e) = self.docker.remove_container(name, Some(rm)).await {
            tracing::warn!(container = name, error = %e, "could not remove sandbox container");
        }
        result
    }

    async fn run_created(&self, spec: &Spec, name: &str) -> anyhow::Result<Outcome> {
        let started = Instant::now();
        self.docker.start_container(name, None).await?;
        let deadline = Duration::from_millis(spec.limits.timeout_ms);
        let mut wait = self
            .docker
            .wait_container(name, None::<WaitContainerOptions>);
        let (exit_code, timed_out) = match tokio::time::timeout(deadline, wait.next()).await {
            Ok(Some(Ok(r))) => (r.status_code, false),
            Ok(Some(Err(DockerError::DockerContainerWaitError { code, .. }))) => (code, false),
            Ok(Some(Err(e))) => return Err(e.into()),
            Ok(None) => (-1, false),
            Err(_) => {
                let kill = KillContainerOptionsBuilder::default()
                    .signal("KILL")
                    .build();
                let _ = self.docker.kill_container(name, Some(kill)).await;
                (137, true)
            }
        };
        let duration_ms = u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX);
        let oom_killed = self
            .docker
            .inspect_container(name, None)
            .await
            .ok()
            .and_then(|c| c.state)
            .and_then(|s| s.oom_killed)
            .unwrap_or(false);
        let logs = LogsOptionsBuilder::default()
            .stdout(true)
            .stderr(true)
            .build();
        let mut stream = self.docker.logs(name, Some(logs));
        let (mut out, mut err) = (Vec::new(), Vec::new());
        while let Some(frame) = stream.next().await {
            match frame? {
                LogOutput::StdOut { message } if out.len() <= MAX_OUTPUT_BYTES => {
                    out.extend_from_slice(&message);
                }
                LogOutput::StdErr { message } if err.len() <= MAX_OUTPUT_BYTES => {
                    err.extend_from_slice(&message);
                }
                _ => {}
            }
        }
        let (stdout, cut_out) = domain::clip(&out, MAX_OUTPUT_BYTES);
        let (stderr, cut_err) = domain::clip(&err, MAX_OUTPUT_BYTES);
        Ok(Outcome {
            exit_code,
            stdout,
            stderr,
            timed_out,
            oom_killed,
            output_truncated: cut_out || cut_err,
            duration_ms,
        })
    }
}

/// sandbox_db: one row per execution (idempotency) and the `usage.sandbox` event.
#[derive(Clone)]
pub struct Executions {
    pub db: Database,
    pub source: String,
}

impl Executions {
    pub fn new(db: Database, cell_id: &str) -> Self {
        Self {
            db,
            source: format!("//{cell_id}/{SOURCE_SERVICE}"),
        }
    }

    /// The stored outcome of an execution, if it ran before.
    pub async fn get(&self, tenant: Uuid, id: Uuid) -> anyhow::Result<Option<Outcome>> {
        let mut tx = self.db.tenant_tx(tenant).await?;
        let row: Option<serde_json::Value> =
            sqlx::query_scalar("SELECT outcome FROM executions WHERE id = $1")
                .bind(id)
                .fetch_optional(&mut *tx)
                .await?;
        Ok(row.map(serde_json::from_value).transpose()?)
    }

    /// Store an outcome with its usage event, once.
    pub async fn record(
        &self,
        tenant: Uuid,
        id: Uuid,
        run_id: &str,
        image: &str,
        outcome: &Outcome,
    ) -> anyhow::Result<()> {
        let mut tx = self.db.tenant_tx(tenant).await?;
        let new = sqlx::query(
            "INSERT INTO executions (id, tenant_id, run_id, image, exit_code, timed_out,
                duration_ms, outcome)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8) ON CONFLICT (id) DO NOTHING",
        )
        .bind(id)
        .bind(tenant)
        .bind(run_id)
        .bind(image)
        .bind(outcome.exit_code)
        .bind(outcome.timed_out)
        .bind(i64::try_from(outcome.duration_ms).unwrap_or(i64::MAX))
        .bind(serde_json::to_value(outcome)?)
        .execute(&mut *tx)
        .await?
        .rows_affected()
            == 1;
        if new {
            let data = json!({"execution_id": id, "run_id": run_id, "image": image,
                              "duration_ms": outcome.duration_ms, "exit_code": outcome.exit_code,
                              "timed_out": outcome.timed_out, "oom_killed": outcome.oom_killed});
            let event = CloudEvent::new("usage.sandbox", &self.source, tenant, data)
                .map_err(|e| anyhow::anyhow!(e))?
                .with_subject(id.to_string());
            enqueue(&mut tx, &event).await?;
        }
        tx.commit().await?;
        Ok(())
    }
}
