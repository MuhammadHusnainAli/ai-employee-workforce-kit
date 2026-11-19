//! TaskService over gRPC (contracts/proto/wfk/task_service/v1/tasks.proto).

use serde_json::json;
use tonic::{Request, Response, Status as RpcStatus};
use uuid::Uuid;

use crate::contracts::wfk::common::v1::TenantContext;
use crate::contracts::wfk::task_service::v1 as pb;
use crate::contracts::wfk::task_service::v1::task_service_server::TaskService;
use crate::task::adapters::{ListFilter, NewTask, TaskError, TaskRow, Tasks};
use crate::task::domain::Status;

const MAX_INPUT: usize = 100_000;

fn status_pb(s: Status) -> pb::TaskStatus {
    match s {
        Status::Queued => pb::TaskStatus::Queued,
        Status::Running => pb::TaskStatus::Running,
        Status::WaitingHuman => pb::TaskStatus::WaitingHuman,
        Status::Completed => pb::TaskStatus::Completed,
        Status::Failed => pb::TaskStatus::Failed,
        Status::Cancelled => pb::TaskStatus::Cancelled,
    }
}

fn status_from_pb(s: i32) -> Option<Status> {
    match pb::TaskStatus::try_from(s).ok()? {
        pb::TaskStatus::Unspecified => None,
        pb::TaskStatus::Queued => Some(Status::Queued),
        pb::TaskStatus::Running => Some(Status::Running),
        pb::TaskStatus::WaitingHuman => Some(Status::WaitingHuman),
        pb::TaskStatus::Completed => Some(Status::Completed),
        pb::TaskStatus::Failed => Some(Status::Failed),
        pb::TaskStatus::Cancelled => Some(Status::Cancelled),
    }
}

fn ts(t: chrono::DateTime<chrono::Utc>) -> prost_types::Timestamp {
    prost_types::Timestamp {
        seconds: t.timestamp(),
        nanos: i32::try_from(t.timestamp_subsec_nanos()).unwrap_or(0),
    }
}

fn task_pb(t: TaskRow) -> pb::Task {
    pb::Task {
        task_id: t.task_id.to_string(),
        employee_id: t.employee_id,
        title: t.title,
        input: t.input,
        priority: t.priority,
        status: status_pb(t.status) as i32,
        stop_reason: t.stop_reason,
        created_by: t.created_by,
        created_at: Some(ts(t.created_at)),
        updated_at: Some(ts(t.updated_at)),
        parent_task_id: t.parent_task_id.map(|u| u.to_string()).unwrap_or_default(),
        root_task_id: t.root_task_id.unwrap_or(t.task_id).to_string(),
        depth: i32::from(t.depth),
        output: t.output,
        delegated_by: t.delegated_by,
    }
}

/// An optional task id: empty means none.
fn optional_task_id(s: &str, field: &str) -> Result<Option<Uuid>, RpcStatus> {
    if s.is_empty() {
        return Ok(None);
    }
    Uuid::parse_str(s)
        .map(Some)
        .map_err(|_| bad(&format!("{field} must be a UUID")))
}

fn bad(msg: &str) -> RpcStatus {
    RpcStatus::invalid_argument(msg)
}

fn tenant(t: Option<&TenantContext>) -> Result<(Uuid, String), RpcStatus> {
    let t = t.ok_or_else(|| bad("tenant is required"))?;
    let id = Uuid::parse_str(&t.tenant_id).map_err(|_| bad("tenant.tenant_id must be a UUID"))?;
    let actor = t
        .actor
        .as_ref()
        .map(|a| a.id.clone())
        .filter(|a| !a.is_empty())
        .unwrap_or_else(|| "unknown".into());
    Ok((id, actor))
}

fn task_id(s: &str) -> Result<Uuid, RpcStatus> {
    Uuid::parse_str(s).map_err(|_| bad("task_id must be a UUID"))
}

fn key(k: Option<&crate::contracts::wfk::common::v1::IdempotencyKey>) -> Result<String, RpcStatus> {
    match k.map(|k| k.value.trim()) {
        Some(v) if !v.is_empty() && v.len() <= 200 => Ok(v.to_string()),
        _ => Err(bad("idempotency_key is required (at most 200 characters)")),
    }
}

impl From<TaskError> for RpcStatus {
    fn from(e: TaskError) -> Self {
        match e {
            TaskError::NotFound(m) => RpcStatus::not_found(m),
            TaskError::Conflict(m) => RpcStatus::already_exists(m),
            TaskError::Precondition(m) => RpcStatus::failed_precondition(m),
            TaskError::Db(e) => {
                tracing::error!(error = %e, "database error");
                RpcStatus::unavailable("database unavailable")
            }
            TaskError::Other(e) => {
                tracing::error!(error = %e, "internal error");
                RpcStatus::internal("internal error")
            }
        }
    }
}

pub struct TaskApi(pub Tasks);

#[tonic::async_trait]
impl TaskService for TaskApi {
    async fn create_task(
        &self,
        req: Request<pb::CreateTaskRequest>,
    ) -> Result<Response<pb::CreateTaskResponse>, RpcStatus> {
        let r = req.into_inner();
        let (tenant, actor) = tenant(r.tenant.as_ref())?;
        let idempotency_key = key(r.idempotency_key.as_ref())?;
        if r.employee_id.trim().is_empty() {
            return Err(bad("employee_id is required"));
        }
        if r.input.trim().is_empty() || r.input.len() > MAX_INPUT {
            return Err(bad("input is required (at most 100,000 characters)"));
        }
        if !(0..=9).contains(&r.priority) {
            return Err(bad("priority must be 0 to 9"));
        }
        let title = if r.title.trim().is_empty() {
            r.input.chars().take(80).collect()
        } else {
            r.title
        };
        let task = self
            .0
            .create(
                tenant,
                NewTask {
                    idempotency_key,
                    employee_id: r.employee_id,
                    title,
                    input: r.input,
                    priority: r.priority,
                    created_by: actor,
                    parent_task_id: optional_task_id(&r.parent_task_id, "parent_task_id")?,
                },
            )
            .await?;
        Ok(Response::new(pb::CreateTaskResponse {
            task: Some(task_pb(task)),
        }))
    }

    async fn get_task(
        &self,
        req: Request<pb::GetTaskRequest>,
    ) -> Result<Response<pb::GetTaskResponse>, RpcStatus> {
        let r = req.into_inner();
        let (tenant, _) = tenant(r.tenant.as_ref())?;
        let task = self.0.get(tenant, task_id(&r.task_id)?).await?;
        Ok(Response::new(pb::GetTaskResponse {
            task: Some(task_pb(task)),
        }))
    }

    async fn list_tasks(
        &self,
        req: Request<pb::ListTasksRequest>,
    ) -> Result<Response<pb::ListTasksResponse>, RpcStatus> {
        let r = req.into_inner();
        let (tenant, _) = tenant(r.tenant.as_ref())?;
        let limit = match r.limit {
            0 => 50,
            n @ 1..=200 => i64::from(n),
            _ => return Err(bad("limit must be 1 to 200")),
        };
        let filter = ListFilter {
            employee_id: Some(r.employee_id.as_str()).filter(|e| !e.is_empty()),
            status: status_from_pb(r.status),
            parent_task_id: optional_task_id(&r.parent_task_id, "parent_task_id")?,
            root_task_id: optional_task_id(&r.root_task_id, "root_task_id")?,
            limit,
        };
        let tasks = self.0.list(tenant, filter).await?;
        Ok(Response::new(pb::ListTasksResponse {
            tasks: tasks.into_iter().map(task_pb).collect(),
        }))
    }

    async fn cancel_task(
        &self,
        req: Request<pb::CancelTaskRequest>,
    ) -> Result<Response<pb::CancelTaskResponse>, RpcStatus> {
        let r = req.into_inner();
        let (tenant, actor) = tenant(r.tenant.as_ref())?;
        let reason = if r.reason.trim().is_empty() {
            "cancelled".to_string()
        } else {
            r.reason
        };
        let task = self
            .0
            .cancel(tenant, task_id(&r.task_id)?, &reason, &actor)
            .await?;
        Ok(Response::new(pb::CancelTaskResponse {
            task: Some(task_pb(task)),
        }))
    }

    async fn decide_approval(
        &self,
        req: Request<pb::DecideApprovalRequest>,
    ) -> Result<Response<pb::DecideApprovalResponse>, RpcStatus> {
        let r = req.into_inner();
        let (tenant, actor) = tenant(r.tenant.as_ref())?;
        let k = key(r.idempotency_key.as_ref())?;
        let data = json!({"approved": r.approved, "by": actor, "note": r.note});
        let task = self
            .0
            .answer(
                tenant,
                task_id(&r.task_id)?,
                &k,
                "task.approval_decided",
                data,
            )
            .await?;
        Ok(Response::new(pb::DecideApprovalResponse {
            task: Some(task_pb(task)),
        }))
    }

    async fn reply_to_task(
        &self,
        req: Request<pb::ReplyToTaskRequest>,
    ) -> Result<Response<pb::ReplyToTaskResponse>, RpcStatus> {
        let r = req.into_inner();
        let (tenant, actor) = tenant(r.tenant.as_ref())?;
        let k = key(r.idempotency_key.as_ref())?;
        if r.text.trim().is_empty() {
            return Err(bad("text is required"));
        }
        let data = json!({"text": r.text, "by": actor});
        let task = self
            .0
            .answer(tenant, task_id(&r.task_id)?, &k, "task.reply_given", data)
            .await?;
        Ok(Response::new(pb::ReplyToTaskResponse {
            task: Some(task_pb(task)),
        }))
    }
}
