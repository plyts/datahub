"""OpenTelemetry tracing of the Data Hub UI and exporter.

Enabled when OTEL_EXPORTER_OTLP_ENDPOINT is set (e.g. http://alloy:4318). Every HTTP call made
with `requests` (Dagster GraphQL, Keycloak, Loki, Prometheus) becomes a span; Alloy turns spans
into RED metrics (rate, errors, duration) stored in Prometheus.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("datahub.telemetry")
_configured = False


def setup_tracing(service_name: str, exporter=None) -> bool:
    """Configure the global tracer provider once. Returns True when tracing is active."""
    global _configured
    if _configured:
        return True
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if exporter is None and not endpoint:
        return False
    from opentelemetry import trace
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    resource = Resource.create(
        {
            "service.name": os.getenv("OTEL_SERVICE_NAME", service_name),
            "service.namespace": "datahub",
            "deployment.environment": os.getenv("DATAHUB_ENV", "dev"),
            "service.version": os.getenv("DATAHUB_VERSION", "1.0.0"),
        }
    )
    provider = TracerProvider(resource=resource)
    if exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    else:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    RequestsInstrumentor().instrument()
    _configured = True
    log.info("OpenTelemetry tracing enabled (service=%s, endpoint=%s)", service_name, endpoint or "custom exporter")
    return True
