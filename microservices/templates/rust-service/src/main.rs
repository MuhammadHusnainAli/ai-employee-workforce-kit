use service_template::platform::{ServiceSettings, init_telemetry, migrate};
use service_template::{SERVICE_NAME, bind, serve};

/// `service-template`          run the service
/// `service-template migrate`  apply pending migrations (creates the database if missing)
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let mut vars: Vec<(String, String)> = std::env::vars().collect();
    if !vars.iter().any(|(k, _)| k == "WFK_SERVICE_NAME") {
        vars.push(("WFK_SERVICE_NAME".into(), SERVICE_NAME.into()));
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

    let (http, grpc) = bind(&settings).await?;
    serve(settings, http, grpc, async {
        let _ = tokio::signal::ctrl_c().await;
        tracing::info!("shutting down");
    })
    .await
}
