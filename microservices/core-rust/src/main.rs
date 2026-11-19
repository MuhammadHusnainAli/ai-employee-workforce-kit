use core_rust::platform::{init_telemetry, migrate};
use core_rust::{Settings, bind, channel, sandbox, serve, task};

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

/// `core-rust`              run the service (every module)
/// `core-rust migrate`      apply each module's pending migrations to its own database
///                          (creates missing databases)
/// `core-rust healthcheck`  exit 0 when the running service is ready (container checks)
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let settings = Settings::from_env()?;
    if std::env::args().nth(1).as_deref() == Some("healthcheck") {
        // For container health checks: the image has no shell or curl.
        let ready = readyz(settings.service.port).await.unwrap_or(false);
        std::process::exit(if ready { 0 } else { 1 });
    }
    let _telemetry = init_telemetry(&settings.service)?;

    if std::env::args().nth(1).as_deref() == Some("migrate") {
        for (module, url, migrator) in [
            ("task", &settings.task_database_url, &task::MIGRATOR),
            (
                "channel",
                &settings.channel_database_url,
                &channel::MIGRATOR,
            ),
            (
                "sandbox",
                &settings.sandbox_database_url,
                &sandbox::MIGRATOR,
            ),
        ] {
            migrate::ensure_database(url).await?;
            let pool = sqlx::PgPool::connect(url).await?;
            let n = migrate::up(migrator, &pool).await?;
            tracing::info!(module, applied = n, "migrations done");
        }
        return Ok(());
    }

    let listener = bind(&settings.service).await?;
    serve(settings, listener, async {
        let _ = tokio::signal::ctrl_c().await;
        tracing::info!("shutting down");
    })
    .await
}
