//! PostgreSQL access with tenant isolation.
//!
//! Every transaction that touches tenant data goes through [`Database::tenant_tx`], which sets
//! `app.tenant_id` for that transaction only (the `SET LOCAL` equivalent). Row-level security
//! policies read it, so a query can never see another tenant's rows.

use std::time::Duration;

use sqlx::postgres::PgPoolOptions;
use sqlx::{PgPool, Postgres, Transaction};
use uuid::Uuid;

/// Outbox and inbox tables. One file serves Python and Rust, so the schema cannot drift.
pub const OUTBOX_INBOX_SQL: &str =
    include_str!("../../../python/wfk-platform/src/wfk_platform/sql/outbox_inbox.sql");

pub type Tx = Transaction<'static, Postgres>;

#[derive(Clone)]
pub struct Database {
    pub pool: PgPool,
}

impl Database {
    pub async fn connect(url: &str, max_connections: u32) -> sqlx::Result<Self> {
        let pool = PgPoolOptions::new()
            .max_connections(max_connections)
            .acquire_timeout(Duration::from_secs(5))
            .connect(url)
            .await?;
        Ok(Self { pool })
    }

    /// A transaction with no tenant set. For cross-tenant infrastructure work only (the outbox
    /// relay, migrations). Dropping it without `commit()` rolls it back.
    pub async fn tx(&self) -> sqlx::Result<Tx> {
        self.pool.begin().await
    }

    /// A transaction scoped to one tenant. Dropping it without `commit()` rolls it back.
    pub async fn tenant_tx(&self, tenant_id: Uuid) -> sqlx::Result<Tx> {
        let mut tx = self.pool.begin().await?;
        sqlx::query("SELECT set_config('app.tenant_id', $1, true)")
            .bind(tenant_id.to_string())
            .execute(&mut *tx)
            .await?;
        Ok(tx)
    }

    /// Readiness check.
    pub async fn ping(&self) -> sqlx::Result<()> {
        sqlx::query("SELECT 1")
            .execute(&self.pool)
            .await
            .map(|_| ())
    }
}
