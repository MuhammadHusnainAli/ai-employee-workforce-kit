//! PostgreSQL access with tenant isolation.
//!
//! Every transaction that touches tenant data goes through [`Database::tenant_tx`], which sets
//! `app.tenant_id` for that transaction only (the `SET LOCAL` equivalent). Row-level security
//! policies read it, so a query can never see another tenant's rows.

use std::time::Duration;

use sqlx::postgres::PgPoolOptions;
use sqlx::{PgPool, Postgres, Transaction};
use uuid::Uuid;

/// Outbox and inbox tables: every module's first migration (tests/core.rs checks they are all
/// the same file), so code and schema cannot drift.
pub const OUTBOX_INBOX_SQL: &str =
    include_str!("../../migrations/task/0001_platform_outbox_inbox.up.sql");

pub type Tx = Transaction<'static, Postgres>;

#[derive(Clone)]
pub struct Database {
    pub pool: PgPool,
    /// Role for tenant transactions (see `ServiceSettings::db_tenant_role`). `None` keeps the
    /// login role, which bypasses row-level security if it owns the tables.
    pub tenant_role: Option<String>,
}

/// A pool that connects on first use, so a service can start while the database is down.
pub fn lazy_pool(url: &str, max_connections: u32) -> sqlx::Result<PgPool> {
    PgPoolOptions::new()
        .max_connections(max_connections)
        .acquire_timeout(Duration::from_secs(5))
        .connect_lazy(url)
}

impl Database {
    pub async fn connect(url: &str, max_connections: u32) -> sqlx::Result<Self> {
        let pool = PgPoolOptions::new()
            .max_connections(max_connections)
            .acquire_timeout(Duration::from_secs(5))
            .connect(url)
            .await?;
        Ok(Self {
            pool,
            tenant_role: None,
        })
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
        if let Some(role) = &self.tenant_role {
            // `SET LOCAL ROLE`, with the role as a bind parameter.
            sqlx::query("SELECT set_config('role', $1, true)")
                .bind(role)
                .execute(&mut *tx)
                .await?;
        }
        Ok(tx)
    }

    /// Use `role` for tenant transactions (empty means none).
    pub fn with_tenant_role(mut self, role: &str) -> Self {
        self.tenant_role = Some(role.to_string()).filter(|r| !r.is_empty());
        self
    }

    /// Readiness check.
    pub async fn ping(&self) -> sqlx::Result<()> {
        sqlx::query("SELECT 1")
            .execute(&self.pool)
            .await
            .map(|_| ())
    }
}
