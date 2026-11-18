use task_service::platform::{ServiceSettings, init_telemetry, migrate};
use task_service::{DATABASE, PORT, SERVICE_NAME, bind, serve};

/// GET /readyz on this service's own port; true on 200.
async fn readyz(port: u16) -> anyhow::Result<bool> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let mut tcp = tokio::net::TcpStream::connect(("127.0.0.1", port)).await?;
    tcp.write_all(b"GET /readyz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        .await?;
    let mut head = [0u8; 12];
    tcp.read_exact(&mut head).await?;
    Ok(&head == b"HTTP/1.1 200")
}

/// `task-service`              run the service
/// `task-service migrate`      apply pending migrations (creates the database if missing)
/// `task-service healthcheck`  exit 0 when the running service is ready (container checks)
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
    if std::env::args().nth(1).as_deref() == Some("healthcheck") {
        // For container health checks: the image has no shell or curl.
        let ready = readyz(settings.port).await.unwrap_or(false);
        std::process::exit(if ready { 0 } else { 1 });
    }
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
