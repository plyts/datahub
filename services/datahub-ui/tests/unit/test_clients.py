import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from datahub_ui.clients.dagster import DagsterClient, DagsterError, key_to_str, metadata_to_dict, str_to_key
from datahub_ui.clients.observability import LokiClient, PrometheusClient, build_logql


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


class RecordingSession:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def post(self, url, json=None, timeout=None):
        self.calls.append(json)
        return FakeResponse(self.payload)


def test_metadata_conversion():
    entries = [
        {"label": "dagster/row_count", "__typename": "IntMetadataEntry", "intValue": 42},
        {"label": "ratio", "__typename": "FloatMetadataEntry", "floatValue": 0.5},
        {"label": "tables", "__typename": "JsonMetadataEntry", "jsonString": '{"a": 1}'},
        {"label": "note", "__typename": "TextMetadataEntry", "text": "hello"},
        {
            "label": "dagster/column_schema",
            "__typename": "TableSchemaMetadataEntry",
            "schema": {"columns": [{"name": "id", "type": "INTEGER", "description": None}]},
        },
    ]
    assert metadata_to_dict(entries) == {
        "dagster/row_count": 42,
        "ratio": 0.5,
        "tables": {"a": 1},
        "note": "hello",
        "dagster/column_schema": [("id", "INTEGER")],
    }


def test_asset_key_helpers():
    assert key_to_str({"path": ["marts", "fct_orders"]}) == "marts/fct_orders"
    assert str_to_key("/marts/fct_orders/") == ["marts", "fct_orders"]


def test_graphql_errors_raise():
    client = DagsterClient("http://x", session=RecordingSession({"errors": [{"message": "Cannot query field"}]}))
    with pytest.raises(DagsterError, match="Cannot query field"):
        client.version()


def test_unexpected_result_type_raises():
    session = RecordingSession({"data": {"launchRun": {"__typename": "PipelineNotFoundError", "message": "nope"}}})
    with pytest.raises(DagsterError, match="nope"):
        DagsterClient("http://x", session=session).launch_job("missing_job")


def test_launch_sends_partition_and_user_tags():
    session = RecordingSession({"data": {"launchRun": {"__typename": "LaunchRunSuccess", "run": {"runId": "abc"}}}})
    client = DagsterClient("http://x", session=session)
    assert client.launch_job("daily", partition_key="2026-01-02", tags={"datahub/user": "alice"}) == "abc"
    params = session.calls[0]["variables"]["p"]
    tags = {t["key"]: t["value"] for t in params["executionMetadata"]["tags"]}
    assert tags == {"datahub/source": "datahub-ui", "datahub/user": "alice", "dagster/partition": "2026-01-02"}
    assert params["selector"] == {
        "repositoryLocationName": "datahub",
        "repositoryName": "__repository__",
        "jobName": "daily",
    }


def test_unreachable_server():
    with pytest.raises(DagsterError, match="injoignable"):
        DagsterClient("http://127.0.0.1:1/graphql", timeout=1).version()
    assert DagsterClient("http://127.0.0.1:1/graphql", timeout=1).ping() is False


def test_logql_builder_escapes_user_input():
    assert build_logql() == '{job="docker"}'
    q = build_logql("datahub-ui", contains='say "hi"', level="error")
    assert q == '{container="datahub-ui"} |~ "(?i)error" |= "say \\"hi\\""'


class _Api(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/v1/query"):
            body = {"status": "success", "data": {"result": [{"metric": {"job": "node"}, "value": [1, "0.25"]}]}}
        elif self.path.startswith("/loki/api/v1/query_range"):
            body = {
                "status": "success",
                "data": {
                    "result": [{"stream": {"container": "datahub-ui"}, "values": [["1700000000000000000", "hello"]]}]
                },
            }
        else:
            self.send_response(200)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def api_url():
    server = HTTPServer(("127.0.0.1", 0), _Api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_prometheus_and_loki_clients_over_http(api_url):
    prom = PrometheusClient(api_url)
    assert prom.query("up") == [{"labels": {"job": "node"}, "value": 0.25}]
    assert prom.scalar("up") == 0.25
    assert prom.ready()
    loki = LokiClient(api_url)
    lines = loki.query_range('{job="docker"}')
    assert lines == [{"ts": 1700000000.0, "line": "hello", "label_container": "datahub-ui"}]
