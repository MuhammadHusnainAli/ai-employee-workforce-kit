//! Tracing and OpenTelemetry: structured logs to stdout, spans exported over OTLP when an
//! endpoint is configured.

use opentelemetry::KeyValue;
use opentelemetry::trace::TracerProvider as _;
use opentelemetry_otlp::WithExportConfig;
use opentelemetry_sdk::Resource;
use opentelemetry_sdk::trace::SdkTracerProvider;
use tracing_subscriber::EnvFilter;
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;

use crate::settings::ServiceSettings;

/// Flushes spans on drop. Keep it alive for the life of the service.
pub struct TelemetryGuard {
    provider: Option<SdkTracerProvider>,
}

impl Drop for TelemetryGuard {
    fn drop(&mut self) {
        if let Some(p) = self.provider.take() {
            let _ = p.shutdown();
        }
    }
}

pub fn resource(settings: &ServiceSettings) -> Resource {
    Resource::builder()
        .with_service_name(settings.service_name.clone())
        .with_attributes([
            KeyValue::new("deployment.environment.name", settings.environment.as_str()),
            KeyValue::new("wfk.cell_id", settings.cell_id.clone()),
        ])
        .build()
}

fn filter(settings: &ServiceSettings) -> EnvFilter {
    let level = match settings.log_level.to_ascii_uppercase().as_str() {
        "WARNING" => "warn".to_string(),
        other => other.to_ascii_lowercase(),
    };
    EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new(level))
}

/// Install the global subscriber. Call once, at startup.
pub fn init_telemetry(settings: &ServiceSettings) -> anyhow::Result<TelemetryGuard> {
    let provider = if settings.otlp_endpoint.is_empty() {
        None
    } else {
        let exporter = opentelemetry_otlp::SpanExporter::builder()
            .with_tonic()
            .with_endpoint(settings.otlp_endpoint.clone())
            .build()?;
        Some(
            SdkTracerProvider::builder()
                .with_resource(resource(settings))
                .with_batch_exporter(exporter)
                .build(),
        )
    };
    let otel_layer = provider.as_ref().map(|p| {
        tracing_opentelemetry::layer().with_tracer(p.tracer(settings.service_name.clone()))
    });

    let registry = tracing_subscriber::registry()
        .with(filter(settings))
        .with(otel_layer);
    if settings.log_json {
        registry
            .with(
                tracing_subscriber::fmt::layer()
                    .json()
                    .with_current_span(true)
                    .with_span_list(false),
            )
            .try_init()?;
    } else {
        registry.with(tracing_subscriber::fmt::layer()).try_init()?;
    }
    Ok(TelemetryGuard { provider })
}
