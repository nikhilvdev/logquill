from __future__ import annotations

import sys
import threading
import types
from typing import Any

import pytest

from logquill import AppriseAlertPlugin, Level, Logger
from logquill.core.records import LogRecord, create_record


class FakeApprise:
    """Stands in for `apprise.Apprise`, recording what would have been sent."""

    def __init__(self, delivers: bool = True) -> None:
        self.delivers = delivers
        self.urls: list[str] = []
        self.sent: list[dict[str, Any]] = []
        self.sent_event = threading.Event()

    def add(self, url: str) -> bool:
        if url.startswith("bad://"):
            return False
        self.urls.append(url)
        return True

    def notify(self, *, body: str, title: str, notify_type: str) -> bool:
        self.sent.append({"body": body, "title": title, "notify_type": notify_type})
        self.sent_event.set()
        return self.delivers


def _record(level: Level = Level.ERROR, message: str = "boom") -> LogRecord:
    return create_record(level=level, logger="app.api", message=message, meta={"secret": "x"})


def test_send_alert_notifies_with_title_body_and_failure_type() -> None:
    client = FakeApprise()
    plugin = AppriseAlertPlugin("json://x", apprise_client=client)

    plugin.send_alert(_record(), 1)

    assert client.sent == [
        {"body": "boom", "title": "[ERROR] app.api", "notify_type": "failure"},
    ]


def test_send_alert_includes_the_occurrence_count_but_not_meta() -> None:
    client = FakeApprise()
    plugin = AppriseAlertPlugin("json://x", apprise_client=client, title="Prod alert")

    plugin.send_alert(_record(), 7)

    assert client.sent[0]["body"] == "boom (x7)"
    assert client.sent[0]["title"] == "Prod alert"
    assert "secret" not in str(client.sent[0])


def test_warn_maps_to_warning_type() -> None:
    client = FakeApprise()
    plugin = AppriseAlertPlugin("json://x", apprise_client=client, threshold="WARN")

    plugin.send_alert(_record(Level.WARN), 1)

    assert client.sent[0]["notify_type"] == "warning"


def test_a_failed_delivery_raises_an_actionable_error() -> None:
    plugin = AppriseAlertPlugin("json://x", apprise_client=FakeApprise(delivers=False))

    with pytest.raises(RuntimeError, match="failed to send"):
        plugin.send_alert(_record(), 1)


def test_an_error_record_alerts_from_a_background_thread_and_failures_reach_on_error() -> None:
    client = FakeApprise(delivers=False)
    errors: list[Exception] = []
    seen = threading.Event()

    class Recording(AppriseAlertPlugin):
        def on_error(self, exc: Exception, record: LogRecord) -> None:
            errors.append(exc)
            seen.set()

    logger = Logger("app", plugins=[Recording("json://x", apprise_client=client)])

    logger.error("boom")

    assert seen.wait(timeout=5)
    assert client.sent[0]["body"] == "boom"
    assert isinstance(errors[0], RuntimeError)


def _install_fake_apprise(monkeypatch: pytest.MonkeyPatch, client: FakeApprise) -> None:
    module = types.ModuleType("apprise")
    module.Apprise = lambda: client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "apprise", module)


def test_urls_are_registered_with_apprise_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeApprise()
    _install_fake_apprise(monkeypatch, client)

    AppriseAlertPlugin(["discord://a/b", "ntfy://topic"])
    AppriseAlertPlugin("json://single")

    assert client.urls == ["discord://a/b", "ntfy://topic", "json://single"]


def test_an_unrecognized_url_fails_at_construction_not_on_the_first_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_apprise(monkeypatch, FakeApprise())

    with pytest.raises(ValueError, match="doesn't recognize the service URL 'bad://nope'"):
        AppriseAlertPlugin("bad://nope")


def test_a_missing_apprise_dependency_gives_an_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "apprise", None)  # makes `import apprise` raise ImportError

    with pytest.raises(ImportError, match=r"pip install logquill\[apprise\]"):
        AppriseAlertPlugin("json://x")


def test_delivers_through_the_real_apprise_library() -> None:
    pytest.importorskip("apprise")
    import json
    from http.server import BaseHTTPRequestHandler, HTTPServer

    received: list[dict[str, Any]] = []
    arrived = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            received.append(json.loads(self.rfile.read(length)))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            arrived.set()

        def log_message(self, format: str, *args: Any) -> None:
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
    try:
        plugin = AppriseAlertPlugin(f"json://127.0.0.1:{httpd.server_port}/hook")
        plugin.send_alert(_record(message="disk full"), 1)
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert arrived.is_set()
    assert received[0]["message"] == "disk full"
    assert received[0]["title"] == "[ERROR] app.api"
    assert received[0]["type"] == "failure"
