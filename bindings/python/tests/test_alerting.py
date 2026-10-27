"""Alert plugins: deduplication and sending run in the Rust core on a bounded pool."""

import json
import socket
import threading
import time

from logquill import (
    AlertingPlugin,
    EmailAlertPlugin,
    Logger,
    PagerDutyAlertPlugin,
    SlackAlertPlugin,
)
from logquill.plugins import pagerduty_alert_plugin


def wait_for(condition, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


class Collect(AlertingPlugin):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.sent = []

    def send_alert(self, record, occurrences):
        self.sent.append((record["message"], occurrences))


def test_the_first_error_alerts_at_once_and_repeats_are_summarised_when_the_window_closes():
    plugin = Collect(dedupe_window_seconds=0.4)
    log = Logger("app", plugins=[plugin], transports=[])
    for _ in range(4):
        log.error("db down")
    log.error("something else")
    log.info("below the threshold")
    assert wait_for(lambda: ("db down", 4) in plugin.sent)
    assert sorted(plugin.sent) == [("db down", 1), ("db down", 4), ("something else", 1)]
    plugin.close()


def test_threshold_is_configurable():
    plugin = Collect(threshold="WARN", dedupe_window_seconds=60)
    log = Logger("app", plugins=[plugin], transports=[])
    log.warn("careful")
    log.info("ignored")
    assert wait_for(lambda: plugin.sent == [("careful", 1)])
    plugin.close()


def test_an_alert_is_not_dropped_when_the_dedupe_table_is_full():
    plugin = Collect(max_tracked_keys=2, dedupe_window_seconds=60)
    log = Logger("app", plugins=[plugin], transports=[])
    for n in range(5):
        log.error(f"distinct {n}")
    assert wait_for(lambda: len(plugin.sent) == 5), "every first occurrence still alerts"
    plugin.close()


def test_a_slow_destination_never_blocks_the_caller():
    release = threading.Event()

    class Slow(AlertingPlugin):
        def send_alert(self, record, occurrences):
            release.wait(10)

    plugin = Slow(dedupe_window_seconds=60)
    log = Logger("app", plugins=[plugin], transports=[])
    started = time.perf_counter()
    for n in range(50):
        log.error(f"e{n}")
    assert time.perf_counter() - started < 2.0
    release.set()
    plugin.close()


def test_a_sender_that_raises_goes_to_on_error_and_does_not_stop_later_alerts():
    errors = []

    class Flaky(AlertingPlugin):
        calls = 0

        def send_alert(self, record, occurrences):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise RuntimeError("webhook down")

        def on_error(self, exc, record):
            errors.append(str(exc))

    plugin = Flaky(dedupe_window_seconds=60)
    log = Logger("app", plugins=[plugin], transports=[])
    log.error("first")
    log.error("second")
    assert wait_for(lambda: Flaky.calls == 2)
    assert errors == ["webhook down"]
    plugin.close()


def test_a_custom_dedupe_key_uses_the_python_implementation():
    plugin = Collect(dedupe_key=lambda r: r["meta"].get("tenant", ""), dedupe_window_seconds=60)
    assert plugin._use_service is False
    log = Logger("app", plugins=[plugin], transports=[])
    log.error("a", tenant="t1")
    log.error("b", tenant="t1")
    log.error("c", tenant="t2")
    assert wait_for(lambda: len(plugin.sent) == 2)
    assert sorted(m for m, _ in plugin.sent) == ["a", "c"]
    plugin.close()


def test_closing_cancels_the_pending_summary():
    plugin = Collect(dedupe_window_seconds=0.3)
    log = Logger("app", plugins=[plugin], transports=[])
    log.error("x")
    log.error("x")
    assert wait_for(lambda: plugin.sent == [("x", 1)])
    plugin.close()
    time.sleep(0.6)
    assert plugin.sent == [("x", 1)], "no summary after close"


def test_slack_posts_a_text_summary_to_the_webhook(recorder):
    plugin = SlackAlertPlugin(f"{recorder.base}/hook", dedupe_window_seconds=0.3)
    log = Logger("svc", plugins=[plugin], transports=[])
    log.error("db down")
    log.error("db down")
    assert wait_for(lambda: len(recorder.requests) == 2)
    texts = sorted(json.loads(body)["text"] for body in recorder.bodies())
    assert texts == ["[ERROR] svc: db down", "[ERROR] svc: db down (x2)"]
    assert recorder.requests[0]["path"] == "/hook"
    plugin.close()


def test_slack_reports_a_rejected_webhook_without_raising(caplog):
    class Rejecting(SlackAlertPlugin):
        pass

    plugin = Rejecting("http://127.0.0.1:1/hook", timeout=0.5, dedupe_window_seconds=60)
    log = Logger("svc", plugins=[plugin], transports=[])
    assert log.error("boom") is not None
    plugin.close()


def test_slack_send_alert_can_be_called_directly_and_raises_when_refused(recorder):
    recorder.status = 404
    plugin = SlackAlertPlugin(f"{recorder.base}/gone")
    try:
        plugin.send_alert({"level": "ERROR", "logger": "x", "message": "m", "meta": {}}, 1)
    except RuntimeError as error:
        assert "404" in str(error) and "webhook" in str(error).lower()
    else:
        raise AssertionError("a refused webhook must raise")


def test_pagerduty_triggers_an_events_api_v2_incident(recorder, monkeypatch):
    monkeypatch.setattr(pagerduty_alert_plugin, "_ENDPOINT", f"{recorder.base}/v2/enqueue")
    plugin = PagerDutyAlertPlugin("routing-key", dedupe_window_seconds=60)
    log = Logger("svc", plugins=[plugin], transports=[])
    log.fatal("out of memory", host="a1")
    assert wait_for(lambda: len(recorder.requests) == 1)
    event = json.loads(recorder.bodies()[0])
    assert event["routing_key"] == "routing-key" and event["event_action"] == "trigger"
    assert event["payload"]["severity"] == "critical" and event["payload"]["source"] == "svc"
    assert event["payload"]["custom_details"]["host"] == "a1"
    plugin.close()


def smtp_server():
    """A minimal SMTP server that accepts one message and returns the DATA it received."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    received = []

    def serve():
        connection, _ = listener.accept()
        reader = connection.makefile("rwb")
        reader.write(b"220 ready\r\n")
        reader.flush()
        data_mode, data = False, []
        for raw in reader:
            line = raw.decode().rstrip("\r\n")
            if data_mode:
                if line == ".":
                    data_mode = False
                    received.append("\n".join(data))
                    reader.write(b"250 queued\r\n")
                else:
                    data.append(line)
            elif line.upper().startswith("EHLO") or line.upper().startswith("HELO"):
                reader.write(b"250 hello\r\n")
            elif line.upper() == "DATA":
                data_mode = True
                reader.write(b"354 go\r\n")
            elif line.upper() == "QUIT":
                reader.write(b"221 bye\r\n")
                reader.flush()
                break
            else:
                reader.write(b"250 ok\r\n")
            reader.flush()
        connection.close()
        listener.close()

    threading.Thread(target=serve, daemon=True).start()
    return listener.getsockname()[1], received


def test_email_sends_one_message_per_alert_over_smtp():
    port, received = smtp_server()
    plugin = EmailAlertPlugin(
        smtp_host="127.0.0.1",
        smtp_port=port,
        from_addr="alerts@example.com",
        to_addrs=["ops@example.com"],
        use_tls=False,
        dedupe_window_seconds=60,
    )
    log = Logger("svc", plugins=[plugin], transports=[])
    log.error("disk full")
    assert wait_for(lambda: received)
    data = received[0]
    assert "Subject: [ERROR] svc" in data and "disk full" in data and "occurrences: 1" in data
    plugin.close()
