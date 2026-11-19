//! The channel module's migrations, against the local stack (`just up`): they apply to the
//! module's own database, isolate tenants and revert.

use sqlx::{Connection, Executor, PgConnection};
use uuid::Uuid;

// This service's own crate, kept in its own block so its position does not depend on the name.
use core_rust::channel::MIGRATOR as CHANNEL_MIGRATOR;
use core_rust::platform::Database;

const T1: Uuid = Uuid::from_u128(0x0193a7c4_0000_7000_8000_000000000001);
const T2: Uuid = Uuid::from_u128(0x0193a7c4_0000_7000_8000_000000000002);
const TEST_DB: &str = "channel_gateway_rs_test";

fn admin_url() -> String {
    std::env::var("WFK_TEST_ADMIN_URL")
        .unwrap_or_else(|_| "postgresql://wfk:wfk@127.0.0.1:7017/postgres".into())
}

fn db_url() -> String {
    let admin = admin_url();
    format!("{}/{TEST_DB}", admin.rsplit_once('/').unwrap().0)
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn channel_migrations_apply_isolate_tenants_and_revert() {
    use core_rust::platform::migrate;
    let url = db_url().replace(TEST_DB, "channel_gateway_rs_migrate_test");
    let mut admin = PgConnection::connect(&admin_url()).await.unwrap();
    admin
        .execute("DROP DATABASE IF EXISTS channel_gateway_rs_migrate_test WITH (FORCE)")
        .await
        .unwrap();
    migrate::ensure_database(&url).await.unwrap();
    let pool = sqlx::PgPool::connect(&url).await.unwrap();

    assert_eq!(migrate::up(&CHANNEL_MIGRATOR, &pool).await.unwrap(), 3);
    assert_eq!(
        migrate::up(&CHANNEL_MIGRATOR, &pool).await.unwrap(),
        0,
        "idempotent"
    );

    // The bindings table enforces tenant isolation for the service role.
    sqlx::query(
        "INSERT INTO bindings (id, tenant_id, kind, employee_id, name, secret, created_by)
         VALUES (gen_random_uuid(), $1, 'webhook', 'e', 'one', 's', 'me'),
                (gen_random_uuid(), $2, 'webhook', 'e', 'two', 's', 'me')",
    )
    .bind(T1)
    .bind(T2)
    .execute(&pool)
    .await
    .unwrap();
    let db = Database {
        pool: pool.clone(),
        tenant_role: None,
    }
    .with_tenant_role("wfk_app");
    let mut tx = db.tenant_tx(T1).await.unwrap();
    let names: Vec<String> = sqlx::query_scalar("SELECT name FROM bindings")
        .fetch_all(&mut *tx)
        .await
        .unwrap();
    assert_eq!(names, vec!["one".to_string()]);
    tx.rollback().await.unwrap();

    migrate::down_to(&CHANNEL_MIGRATOR, &pool, 0).await.unwrap();
    let left: Vec<String> = sqlx::query_scalar(
        "SELECT tablename::text FROM pg_tables WHERE schemaname = 'public' ORDER BY 1",
    )
    .fetch_all(&pool)
    .await
    .unwrap();
    assert_eq!(left, vec!["_sqlx_migrations".to_string()]);
}
