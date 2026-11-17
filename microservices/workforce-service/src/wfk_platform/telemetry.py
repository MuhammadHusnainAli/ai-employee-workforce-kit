"""OpenTelemetry setup: traces and metrics, exported over OTLP when an endpoint is configured."""

from __future__ import annotations

from dataclasses import dataclass

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from wfk_platform.settings import ServiceSettings


@dataclass
class Telemetry:
    tracer_provider: TracerProvider
    meter_provider: MeterProvider

    def shutdown(self) -> None:
        """Flush and stop exporters. Call on service shutdown."""
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()


def build_resource(settings: ServiceSettings) -> Resource:
    return Resource.create(
        {
            "service.name": settings.service_name,
            "deployment.environment.name": settings.environment,
            "wfk.cell_id": settings.cell_id,
        }
    )


def setup_telemetry(settings: ServiceSettings, *, set_global: bool = True) -> Telemetry:
    """Create tracer and meter providers for the service.

    With `otlp_endpoint` empty nothing is exported, which keeps tests and local runs quiet.
    """
    resource = build_resource(settings)
    tracer_provider = TracerProvider(resource=resource)
    readers: list[MetricReader] = []
    if settings.otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otlp_endpoint, insecure=True))
        )
        readers.append(
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=settings.otlp_endpoint, insecure=True)
            )
        )
    meter_provider = MeterProvider(resource=resource, metric_readers=readers)
    if set_global:
        trace.set_tracer_provider(tracer_provider)
        metrics.set_meter_provider(meter_provider)
    return Telemetry(tracer_provider=tracer_provider, meter_provider=meter_provider)
