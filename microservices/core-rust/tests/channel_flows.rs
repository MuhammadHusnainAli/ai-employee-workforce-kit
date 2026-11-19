//! Channel to task and back, against the local stack (`just up`), with a fake task-service and a
//! fake provider. Run with `just test-integration`.

use std::sync::{Arc, Mutex};

use axum::Json;
use axum::http::{HeaderMap, StatusCode};
use serde_json::{Value, json};
use sqlx::{Connection, Executor, PgConnection, Row};
use tokio::net::TcpListener;
use tokio::sync::OnceCell;
use tonic::{Request, Response, Status};
use uuid::Uuid;

// This service's own crate, kept in its own block so its position does not depend on the name.
use core_rust::channel::adapters::{BindingRow, Channels, NewBinding};
use core_rust::channel::domain::{Inbound, Kind, Outbound, verify_wfk};
use core_rust::channel::flows::{Forwarder, RunAnswers, Sender};
use core_rust::contracts::wfk::task_service::v1 as tasks;
use core_rust::contracts::wfk::task_service::v1::task_service_client::TaskServiceClient;
use core_rust::contracts::wfk::task_service::v1::task_service_server::{
    TaskService, TaskServiceServer,
};
use core_rust::platform::{CloudEvent, Database, migrate};

const T1: Uuid = Uuid::from_u128(0x0193a7c4_0000_7000_8000_0000000000d1);
const TEST_DB: &str = "channel_gateway_rs_flows_test";
const SECRET: &str = "s3cret-signing-key";

fn admin_url() -> String {
    std::env::var("WFK_TEST_ADMIN_URL")
        .unwrap_or_else(|_| "postgresql://wfk:wfk@127.0.0.1:7017/postgres".into())
}

fn db_url() -> String {
    format!("{}/{TEST_DB}", admin_url().rsplit_once('/').unwrap().0)
}

static CREATED: OnceCell<()> = OnceCell::const_new();
/// The loops work through queues shared by every test: one test at a time.
static SERIAL: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

async fn db() -> Database {
    CREATED
        .get_or_init(|| async {
            let mut admin = PgConnection::connect(&admin_url()).await.expect("just up");
            admin
                .execute("DROP DATABASE IF EXISTS channel_gateway_rs_flows_test WITH (FORCE)")
                .await
                .unwrap();
            migrate::ensure_database(&db_url()).await.unwrap();
            let pool = sqlx::PgPool::connect(&db_url()).await.unwrap();
            migrate::up(&core_rust::channel::MIGRATOR, &pool)
                .await
                .unwrap();
        })
        .await;
    Database::connect(&db_url(), 5)
        .await
        .unwrap()
        .with_tenant_role("wfk_app")
}

// ---- a fake task-service ------------------------------------------------------------------

#[derive(Clone, Default)]
struct FakeTasks {
    calls: Arc<Mutex<Vec<tasks::CreateTaskRequest>>>,
    /// Answers for the next calls, in order; empty means success.
    script: Arc<Mutex<Vec<Status>>>,
}

#[tonic::async_trait]
impl TaskService for FakeTasks {
    async fn create_task(
        &self,
        req: Request<tasks::CreateTaskRequest>,
    ) -> Result<Response<tasks::CreateTaskResponse>, Status> {
        let r = req.into_inner();
        self.calls.lock().unwrap().push(r.clone());
        let next = {
            let mut s = self.script.lock().unwrap();
            (!s.is_empty()).then(|| s.remove(0))
        };
        if let Some(err) = next {
            return Err(err);
        }
        // Same key, same task: like the real task-service.
        let key = r.idempotency_key.map(|k| k.value).unwrap_or_default();
        Ok(Response::new(tasks::CreateTaskResponse {
            task: Some(tasks::Task {
                task_id: Uuid::new_v5(&Uuid::NAMESPACE_OID, key.as_bytes()).to_string(),
                ..Default::default()
            }),
        }))
    }
    async fn get_task(
        &self,
        _: Request<tasks::GetTaskRequest>,
    ) -> Result<Response<tasks::GetTaskResponse>, Status> {
        Err(Status::unimplemented(""))
    }
    async fn list_tasks(
        &self,
        _: Request<tasks::ListTasksRequest>,
    ) -> Result<Response<tasks::ListTasksResponse>, Status> {
        Err(Status::unimplemented(""))
    }
    async fn cancel_task(
        &self,
        _: Request<tasks::CancelTaskRequest>,
    ) -> Result<Response<tasks::CancelTaskResponse>, Status> {
        Err(Status::unimplemented(""))
    }
    async fn decide_approval(
        &self,
        _: Request<tasks::DecideApprovalRequest>,
    ) -> Result<Response<tasks::DecideApprovalResponse>, Status> {
        Err(Status::unimplemented(""))
    }
    async fn reply_to_task(
        &self,
        _: Request<tasks::ReplyToTaskRequest>,
    ) -> Result<Response<tasks::ReplyToTaskResponse>, Status> {
        Err(Status::unimplemented(""))
    }
}

async fn task_service(fake: FakeTasks) -> TaskServiceClient<tonic::transport::Channel> {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let incoming = tokio_stream_from(listener);
    tokio::spawn(
        tonic::transport::Server::builder()
            .add_service(TaskServiceServer::new(fake))
            .serve_with_incoming(incoming),
    );
    TaskServiceClient::connect(format!("http://{addr}"))
        .await
        .unwrap()
}

fn tokio_stream_from(
    listener: TcpListener,
) -> impl futures::Stream<Item = std::io::Result<tokio::net::TcpStream>> {
    futures::stream::unfold(listener, |l| async move {
        let accepted = l.accept().await.map(|(s, _)| s);
        Some((accepted, l))
    })
}

// ---- a fake provider -----------------------------------------------------------------------

#[derive(Clone, Default)]
struct Provider {
    got: Arc<Mutex<Vec<(HeaderMap, Value)>>>,
    /// Status codes for the next requests; empty means 200 with `answer`.
    script: Arc<Mutex<Vec<StatusCode>>>,
    answer: Arc<Mutex<Value>>,
}

async fn provider(p: Provider) -> String {
    let app = axum::Router::new()
        .route(
            "/reply",
            axum::routing::post(
                |axum::extract::State(p): axum::extract::State<Provider>,
                 headers: HeaderMap,
                 Json(body): Json<Value>| async move {
                    p.got.lock().unwrap().push((headers, body));
                    let code = {
                        let mut s = p.script.lock().unwrap();
                        if s.is_empty() {
                            StatusCode::OK
                        } else {
                            s.remove(0)
                        }
                    };
                    let answer = p.answer.lock().unwrap().clone();
                    (code, Json(answer))
                },
            ),
        )
        .with_state(p);
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    format!("http://{addr}/reply")
}

// ---- helpers --------------------------------------------------------------------------------

async fn binding(c: &Channels, kind: Kind, url: &str) -> BindingRow {
    c.create_binding(
        T1,
        NewBinding {
            idempotency_key: Uuid::new_v4().to_string(),
            kind,
            employee_id: "summariser".into(),
            name: "test".into(),
            secret: SECRET.into(),
            outbound: Outbound {
                url: url.into(),
                token: "xoxb-test".into(),
                from: String::new(),
            },
            created_by: "test".into(),
        },
    )
    .await
    .unwrap()
}

/// Store a message; returns its id and the thread a reply must go to.
async fn message(c: &Channels, b: &BindingRow, text: &str) -> (Uuid, String) {
    let id = Uuid::new_v4().to_string();
    let thread = format!("thread-{id}");
    let rec = c
        .record_inbound(
            b,
            &Inbound {
                provider_message_id: id,
                thread: thread.clone(),
                sender: "ana".into(),
                text: text.into(),
            },
        )
        .await
        .unwrap();
    (rec.inbound_id, thread)
}

async fn inbound(c: &Channels, id: Uuid) -> (String, Option<Uuid>, i32) {
    let mut tx = c.db.tenant_tx(T1).await.unwrap();
    let r = sqlx::query("SELECT status, task_id, attempts FROM inbound_messages WHERE id = $1")
        .bind(id)
        .fetch_one(&mut *tx)
        .await
        .unwrap();
    (r.get("status"), r.get("task_id"), r.get("attempts"))
}

async fn make_due(c: &Channels) {
    sqlx::query("UPDATE forward_queue SET next_attempt_at = now()")
        .execute(&c.db.pool)
        .await
        .unwrap();
    sqlx::query("UPDATE delivery_queue SET next_attempt_at = now()")
        .execute(&c.db.pool)
        .await
        .unwrap();
}

async fn run_event(c: &Channels, event_type: &str, data: Value) {
    let event = CloudEvent::new(event_type, "//test/agent-runtime", T1, data).unwrap();
    let mut tx = c.db.tenant_tx(T1).await.unwrap();
    RunAnswers(c.clone()).apply(&mut tx, &event).await.unwrap();
    tx.commit().await.unwrap();
}

async fn deliveries(c: &Channels, task: Uuid) -> Vec<(String, String, String)> {
    let mut tx = c.db.tenant_tx(T1).await.unwrap();
    sqlx::query_as(
        "SELECT purpose, status, text FROM deliveries WHERE task_id = $1 ORDER BY created_at",
    )
    .bind(task)
    .fetch_all(&mut *tx)
    .await
    .unwrap()
}

// ---- tests ----------------------------------------------------------------------------------

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn a_message_becomes_one_task() {
    let _serial = SERIAL.lock().await;
    let c = Channels::new(db().await, "cell-test");
    let fake = FakeTasks::default();
    let mut f = Forwarder {
        channels: c.clone(),
        tasks: task_service(fake.clone()).await,
    };
    let b = binding(&c, Kind::Webhook, "").await;
    let (id, _) = message(
        &c,
        &b,
        "Summarise the Q3 board deck\nIt is in the shared drive.",
    )
    .await;
    // A busy queue may hold other tests' messages; this one is forwarded either way.
    f.forward_due().await.unwrap();
    let (status, task, _) = inbound(&c, id).await;
    assert_eq!(status, "forwarded");
    let calls = fake.calls.lock().unwrap().clone();
    let call = calls
        .iter()
        .find(|r| r.idempotency_key.as_ref().unwrap().value == format!("channel:{id}"))
        .expect("CreateTask was called for the message");
    assert_eq!(call.employee_id, "summariser");
    assert_eq!(call.title, "Summarise the Q3 board deck");
    assert_eq!(
        call.tenant.as_ref().unwrap().actor.as_ref().unwrap().id,
        "webhook:ana"
    );
    assert!(task.is_some());
    let before = fake.calls.lock().unwrap().len();
    f.forward_due().await.unwrap();
    let again: Vec<_> = fake.calls.lock().unwrap()[before..]
        .iter()
        .filter(|r| r.idempotency_key.as_ref().unwrap().value == format!("channel:{id}"))
        .cloned()
        .collect();
    assert!(again.is_empty(), "forwarded once");
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn task_service_down_is_retried_and_bad_requests_give_up() {
    let _serial = SERIAL.lock().await;
    let c = Channels::new(db().await, "cell-test");
    let fake = FakeTasks::default();
    let mut f = Forwarder {
        channels: c.clone(),
        tasks: task_service(fake.clone()).await,
    };
    let b = binding(&c, Kind::Webhook, "").await;
    let (id, _) = message(&c, &b, "retry me").await;
    fake.script
        .lock()
        .unwrap()
        .push(Status::unavailable("restarting"));
    f.forward_due().await.unwrap();
    assert_eq!(inbound(&c, id).await, ("received".into(), None, 1));
    make_due(&c).await;
    f.forward_due().await.unwrap();
    let (status, task, attempts) = inbound(&c, id).await;
    assert_eq!(
        (status.as_str(), task.is_some(), attempts),
        ("forwarded", true, 2)
    );

    let (bad, _) = message(&c, &b, "bad").await;
    make_due(&c).await;
    fake.script
        .lock()
        .unwrap()
        .push(Status::invalid_argument("no such employee"));
    f.forward_due().await.unwrap();
    assert_eq!(inbound(&c, bad).await.0, "failed", "no point retrying");
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn the_answer_goes_back_to_the_thread_once() {
    let _serial = SERIAL.lock().await;
    let c = Channels::new(db().await, "cell-test");
    let p = Provider::default();
    *p.answer.lock().unwrap() = json!({"id": "msg-77"});
    let url = provider(p.clone()).await;
    let fake = FakeTasks::default();
    let mut f = Forwarder {
        channels: c.clone(),
        tasks: task_service(fake).await,
    };
    let b = binding(&c, Kind::Webhook, &url).await;
    let (id, thread) = message(&c, &b, "What is our refund window?").await;
    f.forward_due().await.unwrap();
    let task = inbound(&c, id).await.1.unwrap();

    let waiting = json!({"run_id": task.to_string(), "seq": 1, "status": "waiting_human"});
    run_event(&c, "run.step_completed", waiting).await;
    let done = json!({"run_id": task.to_string(), "seq": 2, "output": "30 days."});
    run_event(&c, "run.completed", done.clone()).await;
    run_event(&c, "run.completed", done).await; // redelivered
    run_event(
        &c,
        "run.completed",
        json!({"run_id": Uuid::new_v4().to_string()}),
    )
    .await;

    Sender::new(c.clone()).send_due().await.unwrap();
    let got = deliveries(&c, task).await;
    assert_eq!(
        got.iter()
            .map(|d| (d.0.as_str(), d.1.as_str()))
            .collect::<Vec<_>>(),
        vec![("approval_request", "sent"), ("answer", "sent")]
    );
    let sent = p.got.lock().unwrap().clone();
    let (headers, body) = sent.iter().find(|(_, b)| b["text"] == "30 days.").unwrap();
    assert_eq!(
        body["thread"],
        thread.as_str(),
        "back to the thread it came from"
    );
    let raw = serde_json::to_vec(body).unwrap();
    let sig = headers.get("x-wfk-signature").and_then(|v| v.to_str().ok());
    assert_eq!(
        verify_wfk(SECRET, sig, &raw),
        Ok(()),
        "signed, so the receiver can trust it"
    );
    let delivered: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM outbox WHERE type = 'channel.message_delivered'
         AND data->>'provider_ref' = 'msg-77'",
    )
    .fetch_one(&c.db.pool)
    .await
    .unwrap();
    assert!(delivered >= 2);
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn provider_errors_are_retried_and_slack_refusals_count_as_errors() {
    let _serial = SERIAL.lock().await;
    let c = Channels::new(db().await, "cell-test");
    let p = Provider::default();
    let url = provider(p.clone()).await;
    let fake = FakeTasks::default();
    let mut f = Forwarder {
        channels: c.clone(),
        tasks: task_service(fake).await,
    };
    let b = binding(&c, Kind::Slack, &url).await;
    let (id, thread) = message(&c, &b, "hello").await;
    f.forward_due().await.unwrap();
    let task = inbound(&c, id).await.1.unwrap();
    run_event(
        &c,
        "run.completed",
        json!({"run_id": task.to_string(), "output": "Hi!"}),
    )
    .await;

    let s = Sender::new(c.clone());
    p.script
        .lock()
        .unwrap()
        .push(StatusCode::INTERNAL_SERVER_ERROR);
    s.send_due().await.unwrap();
    assert_eq!(deliveries(&c, task).await[0].1, "pending");

    *p.answer.lock().unwrap() = json!({"ok": false, "error": "channel_not_found"});
    make_due(&c).await;
    s.send_due().await.unwrap();
    assert_eq!(
        deliveries(&c, task).await[0].1,
        "pending",
        "Slack's ok:false is a failure"
    );

    *p.answer.lock().unwrap() = json!({"ok": true, "ts": "171.9"});
    make_due(&c).await;
    s.send_due().await.unwrap();
    assert_eq!(deliveries(&c, task).await[0].1, "sent");
    let (headers, body) = p.got.lock().unwrap().last().cloned().unwrap();
    assert_eq!(body["channel"], thread.as_str());
    assert_eq!(
        headers.get("authorization").and_then(|v| v.to_str().ok()),
        Some("Bearer xoxb-test")
    );
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn deliver_message_posts_once_per_key() {
    let _serial = SERIAL.lock().await;
    use core_rust::channel::api::ChannelApi;
    use core_rust::contracts::wfk::channel_gateway::v1 as pb;
    use core_rust::contracts::wfk::channel_gateway::v1::channel_service_server::ChannelService;
    use core_rust::contracts::wfk::common::v1::{IdempotencyKey, TenantContext};

    let c = Channels::new(db().await, "cell-test");
    let p = Provider::default();
    let url = provider(p.clone()).await;
    let b = binding(&c, Kind::Teams, &url).await;
    let req = || pb::DeliverMessageRequest {
        tenant: Some(TenantContext {
            tenant_id: T1.to_string(),
            ..Default::default()
        }),
        idempotency_key: Some(IdempotencyKey {
            value: "weekly-digest-2026-11-18".into(),
        }),
        binding_id: b.id.to_string(),
        thread: "19:team".into(),
        text: "Weekly digest: 3 tasks done.".into(),
    };
    let api = ChannelApi(c.clone());
    let first = api
        .deliver_message(Request::new(req()))
        .await
        .unwrap()
        .into_inner();
    let again = api
        .deliver_message(Request::new(req()))
        .await
        .unwrap()
        .into_inner();
    assert_eq!(first.delivery_id, again.delivery_id);
    Sender::new(c.clone()).send_due().await.unwrap();
    let posted: Vec<Value> = p
        .got
        .lock()
        .unwrap()
        .iter()
        .map(|(_, b)| b.clone())
        .collect();
    assert_eq!(
        posted,
        vec![json!({"text": "Weekly digest: 3 tasks done."})]
    );
}
