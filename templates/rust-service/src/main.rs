use service_template::{SERVICE_NAME, bind, serve};
use wfk_platform::{ServiceSettings, init_telemetry};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let mut vars: Vec<(String, String)> = std::env::vars().collect();
    if !vars.iter().any(|(k, _)| k == "WFK_SERVICE_NAME") {
        vars.push(("WFK_SERVICE_NAME".into(), SERVICE_NAME.into()));
    }
    let settings = ServiceSettings::from_vars(vars)?;
    let _telemetry = init_telemetry(&settings)?;
    let (http, grpc) = bind(&settings).await?;
    serve(settings, http, grpc, async {
        let _ = tokio::signal::ctrl_c().await;
        tracing::info!("shutting down");
    })
    .await
}
