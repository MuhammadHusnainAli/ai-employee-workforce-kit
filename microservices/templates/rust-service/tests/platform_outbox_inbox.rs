//! Integration tests against the local stack (`just up`). Run with `just test-integration`.

use serde_json::json;
use sqlx::{Connection, Executor, PgConnection, Row};
use std::sync::Mutex;
use std::time::Duration;
use tokio::sync::OnceCell;
use uuid::Uuid;

// This service's own crate, kept in its own block so its position does not depend on the name.
use service_template::platform::db::OUTBOX_INBOX_SQL;
use service_template::platform::messaging::{InboxConsumer, OutboxRelay, ensure_stream};
use service_template::platform::{CloudEvent, Database, enqueue};

const T1: Uuid = Uuid::from_u128(0x0193a7c4_0000_7000_8000_000000000001);
const T2: Uuid = Uuid::from_u128(0x0193a7c4_0000_7000_8000_000000000002);
const SOURCE: &str = "//cell-local/test";
const TEST_DB: &str = "service_template_rs_test"; // also spelled out in db()

fn admin_url() -> String {
    std::env::var("WFK_TEST_ADMIN_URL")
        .unwrap_or_else(|_| "postgresql://wfk:wfk@127.0.0.1:5432/postgres".into())
}

fn db_url() -> String {
    let admin = admin_url();
    format!("{}/{TEST_DB}", admin.rsplit_once('/').unwrap().0)
}

fn nats_url() -> String {
    std::env::var("WFK_TEST_NATS_URL").unwrap_or_else(|_| "nats://127.0.0.1:4222".into())
}

static CREATED: OnceCell<()> = OnceCell::const_new();

/// A fresh database with the outbox and inbox tables, created once per test binary.
/// Each test gets its own pool because each `#[tokio::test]` has its own runtime.
async fn db() -> Database {
    CREATED
        .get_or_init(|| async {
            let mut admin = PgConnection::connect(&admin_url()).await.expect("just up");
            admin
                .execute("DROP DATABASE IF EXISTS service_template_rs_test WITH (FORCE)")
                .await
                .unwrap();
            admin
                .execute("CREATE DATABASE service_template_rs_test")
                .await
                .unwrap();
            let mut c = PgConnection::connect(&db_url()).await.unwrap();
            c.execute(OUTBOX_INBOX_SQL).await.unwrap();
        })
        .await;
    Database::connect(&db_url(), 5).await.unwrap()
}

async fn js() -> async_nats::jetstream::Context {
    async_nats::jetstream::new(async_nats::connect(nats_url()).await.expect("just up"))
}

fn unique() -> String {
    Uuid::new_v4().simple().to_string()[..10].to_string()
}

async fn outbox_count(db: &Database, id: Uuid) -> i64 {
    sqlx::query("SELECT count(*) AS n FROM outbox WHERE id = $1")
        .bind(id)
        .fetch_one(&db.pool)
        .await
        .unwrap()
        .get("n")
}

// ---- tenancy ---------------------------------------------------------------------------

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn tenant_tx_sets_tenant_for_that_transaction_only() {
    let db = db().await;
    let mut tx = db.tenant_tx(T1).await.unwrap();
    let t: String = sqlx::query_scalar("SELECT current_setting('app.tenant_id')")
        .fetch_one(&mut *tx)
        .await
        .unwrap();
    assert_eq!(t, T1.to_string());
    tx.commit().await.unwrap();

    let mut tx = db.tx().await.unwrap();
    let t: Option<String> = sqlx::query_scalar("SELECT current_setting('app.tenant_id', true)")
        .fetch_one(&mut *tx)
        .await
        .unwrap();
    assert!(t.unwrap_or_default().is_empty());
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn row_level_security_hides_other_tenants() {
    let db = db().await;
    let mut tx = db.tx().await.unwrap();
    tx.execute(
        "DROP TABLE IF EXISTS rs_notes;
         CREATE TABLE rs_notes (tenant_id uuid NOT NULL, body text NOT NULL);
         ALTER TABLE rs_notes ENABLE ROW LEVEL SECURITY;
         CREATE POLICY tenant_isolation ON rs_notes
             USING (tenant_id = current_setting('app.tenant_id')::uuid)
             WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
         DO $$ BEGIN
             IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'wfk_rls_test') THEN
                 CREATE ROLE wfk_rls_test NOLOGIN;
             END IF;
         END $$;
         GRANT SELECT, INSERT ON rs_notes TO wfk_rls_test;",
    )
    .await
    .unwrap();
    sqlx::query("INSERT INTO rs_notes VALUES ($1, 'tenant one'), ($2, 'tenant two')")
        .bind(T1)
        .bind(T2)
        .execute(&mut *tx)
        .await
        .unwrap();
    tx.commit().await.unwrap();

    // The superuser bypasses RLS, so act as an ordinary role the way services do.
    let mut tx = db.tenant_tx(T1).await.unwrap();
    tx.execute("SET LOCAL ROLE wfk_rls_test").await.unwrap();
    let bodies: Vec<String> = sqlx::query_scalar("SELECT body FROM rs_notes")
        .fetch_all(&mut *tx)
        .await
        .unwrap();
    assert_eq!(bodies, vec!["tenant one".to_string()]);
    let err = sqlx::query("INSERT INTO rs_notes VALUES ($1, 'sneaky')")
        .bind(T2)
        .execute(&mut *tx)
        .await
        .unwrap_err();
    assert!(err.to_string().contains("row-level security"), "{err}");
}

// ---- outbox ----------------------------------------------------------------------------

#[test]
fn event_type_must_be_a_subject() {
    assert!(CloudEvent::new("run.completed", SOURCE, T1, json!({})).is_ok());
    assert!(CloudEvent::new("RunCompleted", SOURCE, T1, json!({})).is_err());
    assert!(CloudEvent::new("run", SOURCE, T1, json!({})).is_err());
    assert!(CloudEvent::new("run.completed", SOURCE, T1, json!([1])).is_err());
}

#[test]
fn event_round_trips_and_ids_are_v7() {
    let e = CloudEvent::new("run.completed", SOURCE, T1, json!({"run_id": "r1"})).unwrap();
    assert_eq!(e.id.get_version_num(), 7);
    assert_eq!(CloudEvent::from_json(&e.to_json()).unwrap(), e);
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn rolled_back_transaction_writes_no_event() {
    let db = db().await;
    let event = CloudEvent::new("test.rolled_back", SOURCE, T1, json!({})).unwrap();
    {
        let mut tx = db.tenant_tx(T1).await.unwrap();
        enqueue(&mut tx, &event).await.unwrap();
        // dropped without commit: rolls back
    }
    assert_eq!(outbox_count(&db, event.id).await, 0);
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn committed_event_is_published_once() {
    let (db, js, u) = (db().await, js().await, unique());
    let subject = format!("rs{u}.created");
    let stream = ensure_stream(&js, &format!("RS_{u}"), vec![format!("rs{u}.>")])
        .await
        .unwrap();
    let event = CloudEvent::new(&subject, SOURCE, T1, json!({"n": 1})).unwrap();
    let mut tx = db.tenant_tx(T1).await.unwrap();
    enqueue(&mut tx, &event).await.unwrap();
    tx.commit().await.unwrap();

    let relay = OutboxRelay::new(db.clone(), js.clone(), 100);
    assert!(relay.publish_pending().await.unwrap() >= 1);
    relay.publish_pending().await.unwrap(); // a second pass must not publish it again

    let published: Option<chrono::DateTime<chrono::Utc>> =
        sqlx::query_scalar("SELECT published_at FROM outbox WHERE id = $1")
            .bind(event.id)
            .fetch_one(&db.pool)
            .await
            .unwrap();
    assert!(published.is_some());
    let mut stream = stream;
    assert_eq!(stream.info().await.unwrap().state.messages, 1);
    let raw = stream
        .get_last_raw_message_by_subject(&subject)
        .await
        .unwrap();
    assert_eq!(CloudEvent::from_json(&raw.payload).unwrap(), event);
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn republish_after_relay_crash_is_deduplicated() {
    let (js, u) = (js().await, unique());
    let mut stream = ensure_stream(&js, &format!("RS_{u}"), vec![format!("rs{u}.>")])
        .await
        .unwrap();
    let event = CloudEvent::new(&format!("rs{u}.created"), SOURCE, T1, json!({})).unwrap();
    let send = || async {
        js.send_publish(
            event.event_type.clone(),
            async_nats::jetstream::message::PublishMessage::build()
                .payload(event.to_json().into())
                .message_id(event.id.to_string()),
        )
        .await
        .unwrap()
        .await
        .unwrap()
    };
    assert!(!send().await.duplicate);
    assert!(send().await.duplicate);
    assert_eq!(stream.info().await.unwrap().state.messages, 1);
}

// ---- inbox -----------------------------------------------------------------------------

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn redelivered_event_runs_handler_once() {
    let (db, js, u) = (db().await, js().await, unique());
    let subject = format!("rs{u}.created");
    let stream = ensure_stream(&js, &format!("RS_{u}"), vec![format!("rs{u}.>")])
        .await
        .unwrap();
    let event = CloudEvent::new(&subject, SOURCE, T1, json!({})).unwrap();
    // Same event delivered twice, as at-least-once delivery allows (distinct Nats-Msg-Ids
    // so JetStream keeps both copies and only the inbox can stop the second).
    for id in ["a", "b"] {
        js.send_publish(
            subject.clone(),
            async_nats::jetstream::message::PublishMessage::build()
                .payload(event.to_json().into())
                .message_id(format!("{id}{u}")),
        )
        .await
        .unwrap()
        .await
        .unwrap();
    }
    let seen = Mutex::new(Vec::new());
    let handler = async |_: &mut PgConnection, e: &CloudEvent| {
        seen.lock().unwrap().push(e.id);
        Ok(())
    };
    let consumer = InboxConsumer::new(db, &stream, &format!("c{u}"), &subject)
        .await
        .unwrap();
    let handled = consumer
        .process_batch(10, Duration::from_secs(2), &handler)
        .await
        .unwrap();
    assert_eq!(handled, 1);
    assert_eq!(*seen.lock().unwrap(), vec![event.id]);
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn failed_handler_leaves_event_unprocessed() {
    let (db, js, u) = (db().await, js().await, unique());
    let subject = format!("rs{u}.created");
    let stream = ensure_stream(&js, &format!("RS_{u}"), vec![format!("rs{u}.>")])
        .await
        .unwrap();
    let event = CloudEvent::new(&subject, SOURCE, T1, json!({})).unwrap();
    js.publish(subject.clone(), event.to_json().into())
        .await
        .unwrap()
        .await
        .unwrap();

    let calls = Mutex::new(0);
    let flaky = async |_: &mut PgConnection, _: &CloudEvent| {
        let mut n = calls.lock().unwrap();
        *n += 1;
        if *n == 1 {
            anyhow::bail!("transient");
        }
        Ok(())
    };
    let consumer = InboxConsumer::new(db, &stream, &format!("c{u}"), &subject)
        .await
        .unwrap();
    let first = consumer
        .process_batch(1, Duration::from_secs(2), &flaky)
        .await
        .unwrap();
    assert_eq!(first, 0, "failed, nak'd, inbox rolled back");
    let second = consumer
        .process_batch(1, Duration::from_secs(5), &flaky)
        .await
        .unwrap();
    assert_eq!(second, 1, "redelivered, now succeeds");
    assert_eq!(*calls.lock().unwrap(), 2);
}

#[test]
fn parses_the_shared_contract_example() {
    // Python services parse the same file (tests/platform/test_ids_and_events.py).
    let raw = include_bytes!("../contracts/events/examples/run.completed.json");
    let event = CloudEvent::from_json(raw).unwrap();
    assert_eq!(event.event_type, "run.completed");
    assert_eq!(event.tenantid, T1);
    assert_eq!(CloudEvent::from_json(&event.to_json()).unwrap(), event);
}

// ---- migrations ------------------------------------------------------------------------

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn migrations_apply_isolate_tenants_and_revert() {
    use service_template::platform::migrate;
    let url = db_url().replace(TEST_DB, "service_template_rs_migrate_test");
    let mut admin = PgConnection::connect(&admin_url()).await.unwrap();
    admin
        .execute("DROP DATABASE IF EXISTS service_template_rs_migrate_test WITH (FORCE)")
        .await
        .unwrap();
    migrate::ensure_database(&url).await.unwrap();
    let pool = sqlx::PgPool::connect(&url).await.unwrap();

    assert_eq!(migrate::up(&pool).await.unwrap(), 2);
    assert_eq!(migrate::up(&pool).await.unwrap(), 0, "idempotent");

    // The example table enforces tenant isolation for the service role.
    sqlx::query("INSERT INTO examples (tenant_id, name) VALUES ($1, 'one'), ($2, 'two')")
        .bind(T1)
        .bind(T2)
        .execute(&pool)
        .await
        .unwrap();
    let db = Database { pool: pool.clone() };
    let mut tx = db.tenant_tx(T1).await.unwrap();
    tx.execute("SET LOCAL ROLE wfk_app").await.unwrap();
    let names: Vec<String> = sqlx::query_scalar("SELECT name FROM examples")
        .fetch_all(&mut *tx)
        .await
        .unwrap();
    assert_eq!(names, vec!["one".to_string()]);
    tx.rollback().await.unwrap();

    migrate::down_to(&pool, 0).await.unwrap();
    let left: Vec<String> = sqlx::query_scalar(
        "SELECT tablename::text FROM pg_tables WHERE schemaname = 'public' ORDER BY 1",
    )
    .fetch_all(&pool)
    .await
    .unwrap();
    assert_eq!(left, vec!["_sqlx_migrations".to_string()]);
}
