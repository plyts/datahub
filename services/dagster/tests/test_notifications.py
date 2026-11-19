import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from datahub_pipelines.notifications import FailureNotice, notify, send_webhook
from datahub_pipelines.settings import Settings

NOTICE = FailureNotice(
    job_name="daily_partition_job", run_id="abc-123", error="boom", env="prod", public_url="http://datahub.local/"
)


def test_message_content():
    text = NOTICE.as_text()
    assert "[Data Hub PROD]" in text
    assert "daily_partition_job" in text
    assert "http://datahub.local/dagster/runs/abc-123" in text
    assert "boom" in text


def test_long_errors_are_truncated():
    notice = FailureNotice("job", "id", "x" * 5000, "dev", "http://h")
    assert len(notice.as_text()) < 1800


class _Recorder(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Recorder.received.append(json.loads(body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def test_webhook_delivery_to_real_http_server():
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/hook"
    assert send_webhook(url, NOTICE) is True
    thread.join(5)
    server.server_close()
    assert "daily_partition_job" in _Recorder.received[-1]["text"]


def test_webhook_failure_never_raises():
    assert send_webhook("http://127.0.0.1:1/unreachable", NOTICE, timeout=1) is False


def test_no_channel_configured():
    settings = Settings(env="dev", notify_webhook_url="", smtp_host="")
    assert notify(settings, NOTICE) == {}
