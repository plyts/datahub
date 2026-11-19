import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from datahub_ui import telemetry
from datahub_ui.clients.dagster import DagsterClient, DagsterError


def test_tracing_disabled_without_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr(telemetry, "_configured", False)
    assert telemetry.setup_tracing("datahub-ui") is False


def test_backend_calls_produce_spans():
    exporter = InMemorySpanExporter()
    assert telemetry.setup_tracing("datahub-ui-test", exporter=exporter)
    with pytest.raises(DagsterError):
        DagsterClient("http://127.0.0.1:1/dagster/graphql", timeout=1).version()
    spans = exporter.get_finished_spans()
    assert spans, "requests instrumentation must emit a span"
    span = spans[-1]
    assert span.name == "POST"
    assert span.attributes.get("url.full", span.attributes.get("http.url", "")).endswith("/dagster/graphql")
    assert span.resource.attributes["service.namespace"] == "datahub"
