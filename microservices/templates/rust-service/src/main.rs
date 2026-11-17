use service_template::platform::{ServiceSettings, init_telemetry, migrate};
use service_template::{DATABASE, PORT, SERVICE_NAME, bind, serve};

/// `service-template`          run the service
/// `service-template migrate`  apply pending migrations (creates the database if missing)
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // This service's defaults; the environment overrides any of them.
    let mut vars: Vec<(String, String)> = std::env::vars().collect();
    for (key, value) in [
        ("WFK_SERVICE_NAME", SERVICE_NAME.to_string()),
        ("WFK_PORT", PORT.to_string()),
        ("WFK_DATABASE_URL", DATABASE.to_string()),
    ] {
        if !vars.iter().any(|(k, _)| k == key) {
            vars.push((key.into(), value));
        }
    }
    let settings = ServiceSettings::from_vars(vars)?;
    let _telemetry = init_telemetry(&settings)?;

    if std::env::args().nth(1).as_deref() == Some("migrate") {
        migrate::ensure_database(&settings.database_url).await?;
        let pool = sqlx::PgPool::connect(&settings.database_url).await?;
        let n = migrate::up(&pool).await?;
        tracing::info!(applied = n, "migrations done");
        return Ok(());
    }

    let listener = bind(&settings).await?;
    serve(settings, listener, async {
        let _ = tokio::signal::ctrl_c().await;
        tracing::info!("shutting down");
    })
    .await
}
