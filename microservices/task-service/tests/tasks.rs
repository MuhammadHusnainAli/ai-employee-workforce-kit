//! TaskService against the local stack (`just up`). Run with `just test-integration`.

use std::time::Duration;

use serde_json::{Value, json};
use sqlx::{Connection, Executor, PgConnection};
use tokio::net::TcpListener;
use tokio::sync::OnceCell;
use tonic::{Code, Request};
use uuid::Uuid;

// This service's own crate, kept in its own block so its position does not depend on the name.
use task_service::adapters::{RunEvents, Tasks};
use task_service::api::TaskApi;
use task_service::contracts::wfk::common::v1::{Actor, IdempotencyKey, TenantContext};
use task_service::contracts::wfk::task_service::v1 as pb;
use task_service::contracts::wfk::task_service::v1::task_service_client::TaskServiceClient;
use task_service::contracts::wfk::task_service::v1::task_service_server::TaskService;
use task_service::platform::messaging::{EventHandler, ensure_stream};
use task_service::platform::{CloudEvent, Database, ServiceSettings, migrate};
use task_service::{RUNS_STREAM, SERVICE_NAME, STREAM, serve};

const T1: Uuid = Uuid::from_u128(0x0193a7c4_0000_7000_8000_0000000000a1);
const T2: Uuid = Uuid::from_u128(0x0193a7c4_0000_7000_8000_0000000000a2);
const TEST_DB: &str = "task_service_rs_api_test";

fn admin_url() -> String {
    std::env::var("WFK_TEST_ADMIN_URL")
        .unwrap_or_else(|_| "postgresql://wfk:wfk@127.0.0.1:7017/postgres".into())
}

fn db_url() -> String {
    format!("{}/{TEST_DB}", admin_url().rsplit_once('/').unwrap().0)
}

fn nats_url() -> String {
    std::env::var("WFK_TEST_NATS_URL").unwrap_or_else(|_| "nats://127.0.0.1:7020".into())
}

static CREATED: OnceCell<()> = OnceCell::const_new();

/// A fresh, migrated database, once per test binary; each test gets its own pool.
async fn db() -> Database {
    CREATED
        .get_or_init(|| async {
            let mut admin = PgConnection::connect(&admin_url()).await.expect("just up");
            admin
                .execute("DROP DATABASE IF EXISTS task_service_rs_api_test WITH (FORCE)")
                .await
                .unwrap();
            migrate::ensure_database(&db_url()).await.unwrap();
            let pool = sqlx::PgPool::connect(&db_url()).await.unwrap();
            migrate::up(&pool).await.unwrap();
        })
        .await;
    Database::connect(&db_url(), 5)
        .await
        .unwrap()
        .with_tenant_role("wfk_app")
}

async fn api() -> TaskApi {
    TaskApi(Tasks::new(db().await, "cell-test"))
}

fn tenant(t: Uuid) -> Option<TenantContext> {
    Some(TenantContext {
        tenant_id: t.to_string(),
        actor: Some(Actor {
            kind: 1,
            id: "alice".into(),
        }),
        ..Default::default()
    })
}

fn key(k: &str) -> Option<IdempotencyKey> {
    Some(IdempotencyKey { value: k.into() })
}

fn unique() -> String {
    Uuid::new_v4().simple().to_string()
}

fn create_req(t: Uuid, k: &str, input: &str) -> pb::CreateTaskRequest {
    pb::CreateTaskRequest {
        tenant: tenant(t),
        idempotency_key: key(k),
        employee_id: "emp-1".into(),
        title: String::new(),
        input: input.into(),
        priority: 3,
    }
}

async fn create(api: &TaskApi, t: Uuid) -> pb::Task {
    api.create_task(Request::new(create_req(
        t,
        &unique(),
        "Write the weekly report",
    )))
    .await
    .unwrap()
    .into_inner()
    .task
    .unwrap()
}

async fn events_for(db: &Database, task_id: &str) -> Vec<(String, Value)> {
    sqlx::query_as("SELECT type, data FROM outbox WHERE subject = $1 ORDER BY seq")
        .bind(task_id)
        .fetch_all(&db.pool)
        .await
        .unwrap()
}

fn run_event(t: Uuid, event_type: &str, data: Value) -> CloudEvent {
    CloudEvent::new(event_type, "//cell-test/agent-runtime", t, data).unwrap()
}

/// Apply a run event the way the inbox consumer does: in a tenant transaction.
async fn apply(db: &Database, event: &CloudEvent) {
    let handler = RunEvents(Tasks::new(db.clone(), "cell-test"));
    let mut tx = db.tenant_tx(event.tenantid).await.unwrap();
    handler.handle(&mut tx, event).await.unwrap();
    tx.commit().await.unwrap();
}

async fn status_of(api: &TaskApi, t: Uuid, task_id: &str) -> (i32, String) {
    let task = api
        .get_task(Request::new(pb::GetTaskRequest {
            tenant: tenant(t),
            task_id: task_id.into(),
        }))
        .await
        .unwrap()
        .into_inner()
        .task
        .unwrap();
    (task.status, task.stop_reason)
}

// ---- create, get, list -------------------------------------------------------------------

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn create_emits_task_created_in_the_same_transaction() {
    let api = api().await;
    let task = create(&api, T1).await;
    assert_eq!(task.status, pb::TaskStatus::Queued as i32);
    assert_eq!(
        task.title, "Write the weekly report",
        "title defaults to the input"
    );
    assert_eq!(task.created_by, "alice");
    let events = events_for(&api.0.db, &task.task_id).await;
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].0, "task.created");
    assert_eq!(events[0].1["task_id"], task.task_id.as_str());
    assert_eq!(events[0].1["input"], "Write the weekly report");
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn same_key_returns_the_same_task_and_emits_once() {
    let api = api().await;
    let k = unique();
    let first = api
        .create_task(Request::new(create_req(T1, &k, "a")))
        .await
        .unwrap()
        .into_inner();
    let again = api
        .create_task(Request::new(create_req(T1, &k, "a")))
        .await
        .unwrap()
        .into_inner();
    assert_eq!(first.task, again.task);
    let id = first.task.unwrap().task_id;
    assert_eq!(events_for(&api.0.db, &id).await.len(), 1);

    let other = api
        .create_task(Request::new(create_req(T1, &k, "something else")))
        .await
        .unwrap_err();
    assert_eq!(other.code(), Code::AlreadyExists);
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn invalid_requests_are_rejected() {
    let api = api().await;
    let mut no_tenant = create_req(T1, "k", "x");
    no_tenant.tenant = None;
    let mut no_key = create_req(T1, "k", "x");
    no_key.idempotency_key = None;
    let mut bad_priority = create_req(T1, "k", "x");
    bad_priority.priority = 10;
    for req in [no_tenant, no_key, bad_priority, create_req(T1, "k", "  ")] {
        let err = api.create_task(Request::new(req)).await.unwrap_err();
        assert_eq!(err.code(), Code::InvalidArgument, "{err:?}");
    }
    let err = api
        .get_task(Request::new(pb::GetTaskRequest {
            tenant: tenant(T1),
            task_id: "not-a-uuid".into(),
        }))
        .await
        .unwrap_err();
    assert_eq!(err.code(), Code::InvalidArgument);
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn other_tenants_tasks_are_invisible() {
    let api = api().await;
    let task = create(&api, T1).await;
    let err = api
        .get_task(Request::new(pb::GetTaskRequest {
            tenant: tenant(T2),
            task_id: task.task_id.clone(),
        }))
        .await
        .unwrap_err();
    assert_eq!(err.code(), Code::NotFound);
    let listed = api
        .list_tasks(Request::new(pb::ListTasksRequest {
            tenant: tenant(T2),
            ..Default::default()
        }))
        .await
        .unwrap()
        .into_inner()
        .tasks;
    assert!(listed.iter().all(|t| t.task_id != task.task_id));
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn list_filters_by_status() {
    let api = api().await;
    let queued = create(&api, T1).await;
    let running = create(&api, T1).await;
    apply(
        &api.0.db,
        &run_event(T1, "run.started", json!({"run_id": running.task_id})),
    )
    .await;
    let ids = |status: pb::TaskStatus| {
        let api = &api;
        async move {
            api.list_tasks(Request::new(pb::ListTasksRequest {
                tenant: tenant(T1),
                status: status as i32,
                limit: 200,
                ..Default::default()
            }))
            .await
            .unwrap()
            .into_inner()
            .tasks
            .into_iter()
            .map(|t| t.task_id)
            .collect::<Vec<_>>()
        }
    };
    let q = ids(pb::TaskStatus::Queued).await;
    let r = ids(pb::TaskStatus::Running).await;
    assert!(q.contains(&queued.task_id) && !q.contains(&running.task_id));
    assert!(r.contains(&running.task_id) && !r.contains(&queued.task_id));
}

// ---- run events move the task ------------------------------------------------------------

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn run_events_project_in_order_and_ignore_late_ones() {
    let api = api().await;
    let task = create(&api, T1).await;
    let id = task.task_id.as_str();
    let db = &api.0.db;

    apply(db, &run_event(T1, "run.started", json!({"run_id": id}))).await;
    assert_eq!(
        status_of(&api, T1, id).await.0,
        pb::TaskStatus::Running as i32
    );

    let waiting = json!({"run_id": id, "seq": 2, "status": "waiting_human", "kind": "ask_human"});
    apply(db, &run_event(T1, "run.step_completed", waiting)).await;
    // A late step (seq 1) arrives after seq 2: ignored.
    let late = json!({"run_id": id, "seq": 1, "status": "running", "kind": "tool"});
    apply(db, &run_event(T1, "run.step_completed", late)).await;
    assert_eq!(
        status_of(&api, T1, id).await.0,
        pb::TaskStatus::WaitingHuman as i32
    );

    let done = json!({"run_id": id, "seq": 3, "status": "completed", "stop_reason": "final"});
    apply(db, &run_event(T1, "run.step_completed", done.clone())).await;
    apply(db, &run_event(T1, "run.completed", done)).await;
    assert_eq!(
        status_of(&api, T1, id).await,
        (pb::TaskStatus::Completed as i32, "final".to_string())
    );
    // A finished task never moves again.
    apply(db, &run_event(T1, "run.started", json!({"run_id": id}))).await;
    assert_eq!(
        status_of(&api, T1, id).await.0,
        pb::TaskStatus::Completed as i32
    );

    let changes: Vec<String> = events_for(db, id)
        .await
        .into_iter()
        .filter(|(t, _)| t == "task.state_changed")
        .map(|(_, d)| d["to"].as_str().unwrap().to_string())
        .collect();
    assert_eq!(changes, ["running", "waiting_human", "completed"]);
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn run_events_for_unknown_runs_change_nothing() {
    let db = db().await;
    apply(
        &db,
        &run_event(T1, "run.started", json!({"run_id": "not-a-task"})),
    )
    .await;
    let unknown = Uuid::now_v7().to_string();
    apply(
        &db,
        &run_event(T1, "run.started", json!({"run_id": unknown})),
    )
    .await;
    assert!(events_for(&db, &unknown).await.is_empty());
}

// ---- commands -----------------------------------------------------------------------------

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn cancel_a_queued_task_finishes_it_at_once() {
    let api = api().await;
    let task = create(&api, T1).await;
    let cancelled = api
        .cancel_task(Request::new(pb::CancelTaskRequest {
            tenant: tenant(T1),
            task_id: task.task_id.clone(),
            reason: "not needed".into(),
        }))
        .await
        .unwrap()
        .into_inner()
        .task
        .unwrap();
    assert_eq!(cancelled.status, pb::TaskStatus::Cancelled as i32);
    let types: Vec<String> = events_for(&api.0.db, &task.task_id)
        .await
        .into_iter()
        .map(|e| e.0)
        .collect();
    assert_eq!(
        types,
        [
            "task.created",
            "task.state_changed",
            "task.cancel_requested"
        ]
    );

    let again = api
        .cancel_task(Request::new(pb::CancelTaskRequest {
            tenant: tenant(T1),
            task_id: task.task_id,
            reason: String::new(),
        }))
        .await
        .unwrap_err();
    assert_eq!(again.code(), Code::FailedPrecondition);
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn cancel_a_running_task_asks_the_run_and_waits_for_it() {
    let api = api().await;
    let task = create(&api, T1).await;
    let id = task.task_id.as_str();
    apply(
        &api.0.db,
        &run_event(T1, "run.started", json!({"run_id": id})),
    )
    .await;
    let resp = api
        .cancel_task(Request::new(pb::CancelTaskRequest {
            tenant: tenant(T1),
            task_id: id.into(),
            reason: String::new(),
        }))
        .await
        .unwrap()
        .into_inner()
        .task
        .unwrap();
    assert_eq!(
        resp.status,
        pb::TaskStatus::Running as i32,
        "the run decides"
    );
    let failed = json!({"run_id": id, "seq": 1, "status": "failed", "stop_reason": "cancelled"});
    apply(&api.0.db, &run_event(T1, "run.failed", failed)).await;
    assert_eq!(
        status_of(&api, T1, id).await.0,
        pb::TaskStatus::Cancelled as i32
    );
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn approval_needs_a_waiting_task_and_is_idempotent() {
    let api = api().await;
    let task = create(&api, T1).await;
    let id = task.task_id.as_str();
    let decide = |k: &str| pb::DecideApprovalRequest {
        tenant: tenant(T1),
        idempotency_key: key(k),
        task_id: id.into(),
        approved: true,
        note: "ok".into(),
    };
    let err = api
        .decide_approval(Request::new(decide("a1")))
        .await
        .unwrap_err();
    assert_eq!(err.code(), Code::FailedPrecondition, "not waiting yet");

    let waiting = json!({"run_id": id, "seq": 1, "status": "waiting_human", "kind": "approval"});
    apply(&api.0.db, &run_event(T1, "run.step_completed", waiting)).await;
    api.decide_approval(Request::new(decide("a1")))
        .await
        .unwrap();
    api.decide_approval(Request::new(decide("a1")))
        .await
        .unwrap();

    let decided: Vec<Value> = events_for(&api.0.db, id)
        .await
        .into_iter()
        .filter(|(t, _)| t == "task.approval_decided")
        .map(|(_, d)| d)
        .collect();
    assert_eq!(decided.len(), 1, "a retry with the same key emits nothing");
    assert_eq!(decided[0]["approved"], true);
    assert_eq!(decided[0]["by"], "alice");
    assert_eq!(decided[0]["task_id"], id);
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn reply_emits_reply_given() {
    let api = api().await;
    let task = create(&api, T1).await;
    let id = task.task_id.as_str();
    let waiting = json!({"run_id": id, "seq": 1, "status": "waiting_human", "kind": "ask_human"});
    apply(&api.0.db, &run_event(T1, "run.step_completed", waiting)).await;
    let reply = |text: &str| pb::ReplyToTaskRequest {
        tenant: tenant(T1),
        idempotency_key: key("r1"),
        task_id: id.into(),
        text: text.into(),
    };
    let err = api
        .reply_to_task(Request::new(reply(" ")))
        .await
        .unwrap_err();
    assert_eq!(err.code(), Code::InvalidArgument);
    api.reply_to_task(Request::new(reply("use the Q3 numbers")))
        .await
        .unwrap();
    let replies: Vec<Value> = events_for(&api.0.db, id)
        .await
        .into_iter()
        .filter(|(t, _)| t == "task.reply_given")
        .map(|(_, d)| d)
        .collect();
    assert_eq!(replies.len(), 1);
    assert_eq!(replies[0]["text"], "use the Q3 numbers");
}

// ---- the running service ------------------------------------------------------------------

/// gRPC on the service port, task.created published to TASKS, run events read from RUNS.
#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn served_task_is_published_and_follows_its_run() {
    db().await; // migrated
    let settings = ServiceSettings::from_vars([
        ("WFK_SERVICE_NAME".to_string(), SERVICE_NAME.to_string()),
        ("WFK_DATABASE_URL".to_string(), db_url()),
        ("WFK_NATS_URL".to_string(), nats_url()),
        ("WFK_CELL_ID".to_string(), "cell-test".to_string()),
    ])
    .unwrap();
    let js = async_nats::jetstream::new(async_nats::connect(nats_url()).await.expect("just up"));
    ensure_stream(&js, RUNS_STREAM, vec!["run.>".into()])
        .await
        .unwrap();

    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let (stop, stopped) = tokio::sync::oneshot::channel::<()>();
    let server = tokio::spawn(serve(settings, listener, async {
        let _ = stopped.await;
    }));

    let mut client = TaskServiceClient::connect(format!("http://{addr}"))
        .await
        .unwrap();
    let task = client
        .create_task(create_req(T1, &unique(), "Summarise the inbox"))
        .await
        .unwrap()
        .into_inner()
        .task
        .unwrap();

    // task.created reaches the TASKS stream.
    let tasks_stream = loop {
        if let Ok(s) = js.get_stream(STREAM).await {
            break s;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    };
    let mut published = false;
    for _ in 0..50 {
        let found = tasks_stream
            .get_last_raw_message_by_subject("task.created")
            .await
            .ok()
            .and_then(|m| CloudEvent::from_json(&m.payload).ok())
            .is_some_and(|e| e.subject.as_deref() == Some(task.task_id.as_str()));
        if found {
            published = true;
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    assert!(published, "task.created was not published");

    // agent-runtime's run.started moves the task to running.
    let started = run_event(T1, "run.started", json!({"run_id": task.task_id}));
    js.publish("run.started", started.to_json().into())
        .await
        .unwrap()
        .await
        .unwrap();
    let mut status = 0;
    for _ in 0..100 {
        status = client
            .get_task(pb::GetTaskRequest {
                tenant: tenant(T1),
                task_id: task.task_id.clone(),
            })
            .await
            .unwrap()
            .into_inner()
            .task
            .unwrap()
            .status;
        if status == pb::TaskStatus::Running as i32 {
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    assert_eq!(status, pb::TaskStatus::Running as i32);

    stop.send(()).unwrap();
    tokio::time::timeout(Duration::from_secs(10), server)
        .await
        .expect("shuts down")
        .unwrap()
        .unwrap();
}
