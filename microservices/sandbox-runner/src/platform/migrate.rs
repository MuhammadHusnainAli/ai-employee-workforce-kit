//! Database migrations, built into the binary: `sandbox-runner migrate`.
//!
//! Uses the same `NNNN_name.up.sql` / `NNNN_name.down.sql` files as Python services. Each
//! migration runs in a transaction, and sqlx refuses to run when an applied migration's
//! checksum changed (never edit an applied migration; add a new one).

use sqlx::migrate::{MigrateDatabase, Migrator};
use sqlx::{PgPool, Postgres};

/// This service's migrations, embedded at compile time from `migrations/`.
pub static MIGRATOR: Migrator = sqlx::migrate!("./migrations");

/// Create the database if it does not exist (local and test use).
pub async fn ensure_database(url: &str) -> sqlx::Result<()> {
    if !Postgres::database_exists(url).await? {
        Postgres::create_database(url).await?;
    }
    Ok(())
}

/// Apply every pending migration. Returns the number applied.
pub async fn up(pool: &PgPool) -> anyhow::Result<usize> {
    let before = applied(pool).await?;
    MIGRATOR.run(pool).await?;
    Ok(applied(pool).await? - before)
}

/// Revert down to (and excluding) `target` version; `0` reverts everything.
pub async fn down_to(pool: &PgPool, target: i64) -> anyhow::Result<()> {
    MIGRATOR.undo(pool, target).await?;
    Ok(())
}

async fn applied(pool: &PgPool) -> sqlx::Result<usize> {
    let exists: bool = sqlx::query_scalar("SELECT to_regclass('_sqlx_migrations') IS NOT NULL")
        .fetch_one(pool)
        .await?;
    if !exists {
        return Ok(0);
    }
    let n: i64 = sqlx::query_scalar("SELECT count(*) FROM _sqlx_migrations WHERE success")
        .fetch_one(pool)
        .await?;
    Ok(usize::try_from(n).unwrap_or(0))
}
