//! task_db: tasks, their events (through the outbox), and the run-event projection.

use chrono::{DateTime, Utc};
use futures::future::BoxFuture;
use serde_json::json;
use sqlx::{PgConnection, Row};
use uuid::Uuid;

use crate::platform::messaging::EventHandler;
use crate::platform::{CloudEvent, Database, enqueue};
use crate::task::domain::{self, Status};

pub const SOURCE_SERVICE: &str = "task-service";

#[derive(Debug, thiserror::Error)]
pub enum TaskError {
    #[error("{0}")]
    NotFound(String),
    #[error("{0}")]
    Conflict(String),
    #[error("{0}")]
    Precondition(String),
    #[error(transparent)]
    Db(#[from] sqlx::Error),
    #[error(transparent)]
    Other(#[from] anyhow::Error),
}

#[derive(Debug, Clone, PartialEq)]
pub struct TaskRow {
    pub task_id: Uuid,
    pub tenant_id: Uuid,
    pub employee_id: String,
    pub title: String,
    pub input: String,
    pub priority: i32,
    pub status: Status,
    pub stop_reason: String,
    pub run_seq: i32,
    pub created_by: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub parent_task_id: Option<Uuid>,
    pub root_task_id: Option<Uuid>,
    pub depth: i16,
    pub output: String,
    pub delegated_by: String,
}

impl TaskRow {
    fn from_row(r: &sqlx::postgres::PgRow) -> Result<Self, sqlx::Error> {
        let status: String = r.try_get("status")?;
        Ok(Self {
            task_id: r.try_get("task_id")?,
            tenant_id: r.try_get("tenant_id")?,
            employee_id: r.try_get("employee_id")?,
            title: r.try_get("title")?,
            input: r.try_get("input")?,
            priority: r.try_get("priority")?,
            status: Status::parse(&status).unwrap_or(Status::Failed),
            stop_reason: r.try_get("stop_reason")?,
            run_seq: r.try_get("run_seq")?,
            created_by: r.try_get("created_by")?,
            created_at: r.try_get("created_at")?,
            updated_at: r.try_get("updated_at")?,
            parent_task_id: r.try_get("parent_task_id")?,
            root_task_id: r.try_get("root_task_id")?,
            depth: r.try_get("depth")?,
            output: r.try_get("output")?,
            delegated_by: r.try_get("delegated_by")?,
        })
    }
}

pub struct NewTask {
    pub idempotency_key: String,
    pub employee_id: String,
    pub title: String,
    pub input: String,
    pub priority: i32,
    pub created_by: String,
    /// Hand the task down from this one (delegation).
    pub parent_task_id: Option<Uuid>,
}

/// What ListTasks narrows to. `None` means any.
#[derive(Debug, Default, Clone, Copy)]
pub struct ListFilter<'a> {
    pub employee_id: Option<&'a str>,
    pub status: Option<Status>,
    pub parent_task_id: Option<Uuid>,
    pub root_task_id: Option<Uuid>,
    pub limit: i64,
}

#[derive(Clone)]
pub struct Tasks {
    pub db: Database,
    pub source: String,
}

async fn fetch(conn: &mut PgConnection, task_id: Uuid, lock: bool) -> Result<TaskRow, TaskError> {
    let sql = if lock {
        "SELECT * FROM tasks WHERE task_id = $1 FOR UPDATE"
    } else {
        "SELECT * FROM tasks WHERE task_id = $1"
    };
    let row = sqlx::query(sql)
        .bind(task_id)
        .fetch_optional(&mut *conn)
        .await?;
    match row {
        Some(r) => Ok(TaskRow::from_row(&r)?),
        None => Err(TaskError::NotFound(format!("no task {task_id}"))),
    }
}

impl Tasks {
    pub fn new(db: Database, cell_id: &str) -> Self {
        Self {
            db,
            source: format!("//{cell_id}/{SOURCE_SERVICE}"),
        }
    }

    async fn emit(
        &self,
        conn: &mut PgConnection,
        tenant: Uuid,
        event_type: &str,
        task_id: Uuid,
        data: serde_json::Value,
        id: Option<Uuid>,
    ) -> Result<(), TaskError> {
        let mut event = CloudEvent::new(event_type, &self.source, tenant, data)
            .map_err(|e| anyhow::anyhow!(e))?
            .with_subject(task_id.to_string());
        if let Some(id) = id {
            event.id = id;
        }
        enqueue(conn, &event).await?;
        Ok(())
    }

    /// Store a task and emit task.created (which starts its run). The same key returns the
    /// same task; reusing a key for a different task is a conflict.
    pub async fn create(&self, tenant: Uuid, new: NewTask) -> Result<TaskRow, TaskError> {
        let mut tx = self.db.tenant_tx(tenant).await?;
        let task_id = Uuid::now_v7();
        let (root, depth, delegated_by) = match new.parent_task_id {
            None => (task_id, 0, String::new()),
            Some(parent_id) => {
                // Locked, so two sub-tasks created at once cannot both pass the cap.
                let parent = fetch(&mut tx, parent_id, true).await?;
                let children: i64 =
                    sqlx::query_scalar("SELECT count(*) FROM tasks WHERE parent_task_id = $1")
                        .bind(parent_id)
                        .fetch_one(&mut *tx)
                        .await?;
                let depth = domain::check_child(parent.status, parent.depth, children)
                    .map_err(TaskError::Precondition)?;
                let root = parent.root_task_id.unwrap_or(parent.task_id);
                (root, depth, new.created_by.clone())
            }
        };
        let inserted = sqlx::query(
            "INSERT INTO tasks (task_id, tenant_id, employee_id, title, input, priority,
                idempotency_key, created_by, parent_task_id, root_task_id, depth, delegated_by)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
             ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING *",
        )
        .bind(task_id)
        .bind(tenant)
        .bind(&new.employee_id)
        .bind(&new.title)
        .bind(&new.input)
        .bind(new.priority)
        .bind(&new.idempotency_key)
        .bind(&new.created_by)
        .bind(new.parent_task_id)
        .bind(root)
        .bind(depth)
        .bind(&delegated_by)
        .fetch_optional(&mut *tx)
        .await?;
        let task = match inserted {
            Some(r) => {
                let task = TaskRow::from_row(&r)?;
                let data = json!({
                    "task_id": task.task_id, "employee_id": task.employee_id,
                    "title": task.title, "input": task.input, "priority": task.priority,
                    "parent_task_id": task.parent_task_id, "root_task_id": task.root_task_id,
                    "depth": task.depth,
                });
                self.emit(&mut tx, tenant, "task.created", task.task_id, data, None)
                    .await?;
                task
            }
            None => {
                let r = sqlx::query("SELECT * FROM tasks WHERE idempotency_key = $1")
                    .bind(&new.idempotency_key)
                    .fetch_one(&mut *tx)
                    .await?;
                let task = TaskRow::from_row(&r)?;
                if task.employee_id != new.employee_id
                    || task.input != new.input
                    || task.parent_task_id != new.parent_task_id
                {
                    return Err(TaskError::Conflict(
                        "idempotency key already used for a different task".into(),
                    ));
                }
                task
            }
        };
        tx.commit().await?;
        Ok(task)
    }

    pub async fn get(&self, tenant: Uuid, task_id: Uuid) -> Result<TaskRow, TaskError> {
        let mut tx = self.db.tenant_tx(tenant).await?;
        fetch(&mut tx, task_id, false).await
    }

    pub async fn list(
        &self,
        tenant: Uuid,
        filter: ListFilter<'_>,
    ) -> Result<Vec<TaskRow>, TaskError> {
        let mut tx = self.db.tenant_tx(tenant).await?;
        let rows = sqlx::query(
            "SELECT * FROM tasks
             WHERE ($1::text IS NULL OR employee_id = $1) AND ($2::text IS NULL OR status = $2)
               AND ($4::uuid IS NULL OR parent_task_id = $4)
               AND ($5::uuid IS NULL OR root_task_id = $5 OR task_id = $5)
             ORDER BY created_at DESC LIMIT $3",
        )
        .bind(filter.employee_id)
        .bind(filter.status.map(Status::as_str))
        .bind(filter.limit)
        .bind(filter.parent_task_id)
        .bind(filter.root_task_id)
        .fetch_all(&mut *tx)
        .await?;
        rows.iter()
            .map(|r| TaskRow::from_row(r).map_err(TaskError::from))
            .collect()
    }

    /// Ask for the run to be cancelled. A queued task (no run yet) is cancelled at once.
    pub async fn cancel(
        &self,
        tenant: Uuid,
        task_id: Uuid,
        reason: &str,
        by: &str,
    ) -> Result<TaskRow, TaskError> {
        let mut tx = self.db.tenant_tx(tenant).await?;
        let mut task = fetch(&mut tx, task_id, true).await?;
        if task.status.is_finished() {
            return Err(TaskError::Precondition(format!(
                "task is already {}",
                task.status.as_str()
            )));
        }
        task = self.cancel_one(&mut tx, task, reason, by).await?;
        // Everything handed down from it stops too.
        let below = sqlx::query(
            "WITH RECURSIVE below AS (
                 SELECT task_id FROM tasks WHERE parent_task_id = $1
                 UNION ALL
                 SELECT t.task_id FROM tasks t JOIN below b ON t.parent_task_id = b.task_id)
             SELECT * FROM tasks WHERE task_id IN (SELECT task_id FROM below)
               AND status NOT IN ('completed', 'failed', 'cancelled')
             ORDER BY depth, created_at FOR UPDATE",
        )
        .bind(task_id)
        .fetch_all(&mut *tx)
        .await?;
        for r in &below {
            self.cancel_one(&mut tx, TaskRow::from_row(r)?, reason, by)
                .await?;
        }
        tx.commit().await?;
        Ok(task)
    }

    /// Ask one task's run to stop; a queued task (no run yet) is cancelled at once.
    async fn cancel_one(
        &self,
        conn: &mut PgConnection,
        task: TaskRow,
        reason: &str,
        by: &str,
    ) -> Result<TaskRow, TaskError> {
        let task = if task.status == Status::Queued {
            self.set_status(
                conn,
                &task,
                Status::Cancelled,
                "cancelled",
                task.run_seq,
                None,
            )
            .await?
        } else {
            task
        };
        let data = json!({"task_id": task.task_id, "reason": reason, "by": by});
        self.emit(
            conn,
            task.tenant_id,
            "task.cancel_requested",
            task.task_id,
            data,
            None,
        )
        .await?;
        Ok(task)
    }

    /// A person's answer for the waiting run: approve/decline, or a reply. Idempotent on `key`:
    /// the event id is derived from it, so a retry emits nothing new.
    pub async fn answer(
        &self,
        tenant: Uuid,
        task_id: Uuid,
        key: &str,
        event_type: &str,
        mut data: serde_json::Value,
    ) -> Result<TaskRow, TaskError> {
        let mut tx = self.db.tenant_tx(tenant).await?;
        let task = fetch(&mut tx, task_id, true).await?;
        let event_id = Uuid::new_v5(&task_id, key.as_bytes());
        let seen: Option<i64> = sqlx::query_scalar("SELECT seq FROM outbox WHERE id = $1")
            .bind(event_id)
            .fetch_optional(&mut *tx)
            .await?;
        if seen.is_some() {
            return Ok(task); // already answered with this key
        }
        if task.status != Status::WaitingHuman {
            return Err(TaskError::Precondition(format!(
                "task is {}, not waiting for a person",
                task.status.as_str()
            )));
        }
        data["task_id"] = json!(task_id);
        self.emit(&mut tx, tenant, event_type, task_id, data, Some(event_id))
            .await?;
        tx.commit().await?;
        Ok(task)
    }

    async fn set_status(
        &self,
        conn: &mut PgConnection,
        task: &TaskRow,
        status: Status,
        stop_reason: &str,
        run_seq: i32,
        output: Option<&str>,
    ) -> Result<TaskRow, TaskError> {
        let r = sqlx::query(
            "UPDATE tasks SET status = $2, stop_reason = $3, run_seq = $4, updated_at = now(),
                 output = coalesce($5, output)
             WHERE task_id = $1 RETURNING *",
        )
        .bind(task.task_id)
        .bind(status.as_str())
        .bind(stop_reason)
        .bind(run_seq)
        .bind(output)
        .fetch_one(&mut *conn)
        .await?;
        let updated = TaskRow::from_row(&r)?;
        if updated.status != task.status {
            let data = json!({
                "task_id": task.task_id, "from": task.status.as_str(),
                "to": status.as_str(), "stop_reason": stop_reason,
            });
            self.emit(
                conn,
                task.tenant_id,
                "task.state_changed",
                task.task_id,
                data,
                None,
            )
            .await?;
            if let (Some(parent), true) = (updated.parent_task_id, status.is_finished()) {
                // Addressed to the parent: its run waits for this.
                let data = json!({
                    "task_id": parent, "child_task_id": updated.task_id,
                    "employee_id": updated.employee_id, "title": updated.title,
                    "status": status.as_str(), "stop_reason": stop_reason,
                    "output": domain::child_output(&updated.output),
                });
                self.emit(
                    conn,
                    task.tenant_id,
                    "task.child_finished",
                    parent,
                    data,
                    None,
                )
                .await?;
            }
        }
        Ok(updated)
    }

    /// Apply one run event (inside the inbox transaction). Unknown, stale or duplicate events
    /// change nothing.
    pub async fn apply_run_event(
        &self,
        conn: &mut PgConnection,
        event: &CloudEvent,
    ) -> Result<(), TaskError> {
        let Some(update) = domain::run_update(&event.event_type, &event.data) else {
            return Ok(());
        };
        let Some(run_id) = event.data.get("run_id").and_then(|v| v.as_str()) else {
            return Ok(());
        };
        let Ok(task_id) = Uuid::parse_str(run_id) else {
            return Ok(()); // a run that did not come from a task
        };
        let task = match fetch(conn, task_id, true).await {
            Ok(t) => t,
            Err(TaskError::NotFound(_)) => return Ok(()),
            Err(e) => return Err(e),
        };
        if !domain::applies(task.status, i64::from(task.run_seq), &update) {
            return Ok(());
        }
        let seq = i32::try_from(update.seq).unwrap_or(i32::MAX);
        let output = event
            .data
            .get("output")
            .and_then(|v| v.as_str())
            .filter(|_| update.status.is_finished());
        self.set_status(conn, &task, update.status, &update.stop_reason, seq, output)
            .await?;
        Ok(())
    }
}

/// Consumes run.* events from agent-runtime's RUNS stream.
pub struct RunEvents(pub Tasks);

impl EventHandler for RunEvents {
    fn handle<'a>(
        &'a self,
        conn: &'a mut PgConnection,
        event: &'a CloudEvent,
    ) -> BoxFuture<'a, anyhow::Result<()>> {
        Box::pin(async move {
            self.0
                .apply_run_event(conn, event)
                .await
                .map_err(|e| anyhow::anyhow!(e))
        })
    }
}
