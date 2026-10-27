"""The metrics bridge and runtime level control."""

import logging
import os
import signal
import socket
import time

import pytest

from logquill import (
    LevelEnvWatcher,
    LevelFileWatcher,
    Logger,
    MetricsPlugin,
    PrometheusPushgatewayBackend,
    StatsDBackend,
    install_signal_level_handler,
)
from logquill.core.levels import Level

# ---- metrics ----------------------------------------------------------------------------------


def test_errors_and_durations_reach_statsd_over_udp():
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(5)
    backend = StatsDBackend(host="127.0.0.1", port=receiver.getsockname()[1], prefix="svc")
    log = Logger("agent", plugins=[MetricsPlugin(backend)], transports=[], async_dispatch=False)
    log.error("boom")
    log.info("done", duration_ms=12.5)
    log.info("nothing to report")
    lines = sorted(receiver.recv(1024).decode() for _ in range(2))
    assert lines == ["svc.logquill.duration_ms:12.5|h|#logger:agent", "svc.logquill.errors:1|c|#logger:agent"]
    receiver.settimeout(0.2)
    with pytest.raises(socket.timeout):
        receiver.recv(1024)


def test_statsd_with_an_injected_socket_keeps_the_python_path():
    sent = []

    class Fake:
        def sendto(self, data, address):
            sent.append((data.decode(), address))

        def close(self):
            pass

    backend = StatsDBackend(host="h", port=1, sock=Fake())
    backend.increment("m", tags={"a": "b"})
    assert sent == [("m:1|c|#a:b", ("h", 1))]


def test_the_pushgateway_accumulates_and_puts_cumulative_totals(recorder):
    backend = PrometheusPushgatewayBackend(recorder.base, job_name="myjob")
    plugin = MetricsPlugin(backend)
    log = Logger("agent", plugins=[plugin], transports=[], async_dispatch=False)
    log.error("a")
    log.error("b")
    log.info("span", duration_ms=10.0)
    log.info("span", duration_ms=30.0)
    assert recorder.requests == [], "nothing is sent until a push"
    backend.push()
    body = recorder.bodies()[0]
    assert recorder.requests[0]["path"] == "/metrics/job/myjob"
    assert 'logquill_errors{logger="agent"} 2.0' in body
    assert 'logquill_duration_ms_sum{logger="agent"} 40.0' in body
    assert 'logquill_duration_ms_count{logger="agent"} 2' in body
    log.error("c")
    plugin.close()
    assert 'logquill_errors{logger="agent"} 3.0' in recorder.bodies()[1], "totals are cumulative"


def test_a_refusing_pushgateway_raises_with_an_actionable_message(recorder):
    recorder.status = 500
    backend = PrometheusPushgatewayBackend(recorder.base)
    backend.increment("m", tags={})
    with pytest.raises(RuntimeError, match="pushgateway returned HTTP 500"):
        backend.push()


def test_the_pushgateway_keeps_a_bounded_number_of_series(recorder):
    backend = PrometheusPushgatewayBackend(recorder.base, max_series=2)
    for logger in ("a", "b", "c", "a"):
        backend.increment("m", tags={"logger": logger})
    backend.push()
    body = recorder.bodies()[0]
    assert 'logger="a"} 2.0' in body and 'logger="b"' in body and 'logger="c"' not in body


def test_a_pushgateway_sender_you_inject_keeps_the_python_path():
    sent = []
    backend = PrometheusPushgatewayBackend("http://gw/", sender=lambda url, body: sent.append((url, body)))
    backend.increment("m", tags={})
    backend.push()
    assert sent[0][0] == "http://gw/metrics/job/logquill"


# ---- runtime level control --------------------------------------------------------------------


def test_a_file_watcher_applies_the_first_value_then_only_changes(tmp_path, caplog):
    path = tmp_path / "level"
    log = Logger("app", level="INFO")
    watcher = LevelFileWatcher(log, path)
    watcher.poll_once()
    assert log.level is Level.INFO, "a missing file means no change"
    path.write_text("debug\n")
    watcher.poll_once()
    assert log.level is Level.DEBUG
    log.set_level("ERROR")
    watcher.poll_once()
    assert log.level is Level.ERROR, "the same text is not applied twice"
    path.write_text("shouting")
    with caplog.at_level(logging.WARNING, logger="logquill"):
        watcher.poll_once()
    assert log.level is Level.ERROR and "not a valid level" in caplog.text
    path.write_text("30")
    watcher.poll_once()
    assert log.level is Level.WARN


def test_an_env_watcher_follows_a_variable_changed_inside_the_process(monkeypatch):
    log = Logger("app", level="INFO")
    watcher = LevelEnvWatcher(log, "MY_APP_LEVEL")
    watcher.poll_once()
    assert log.level is Level.INFO
    monkeypatch.setenv("MY_APP_LEVEL", "TRACE")
    watcher.poll_once()
    assert log.level is Level.TRACE


def test_the_polling_thread_picks_up_a_change_and_stops_cleanly(tmp_path):
    path = tmp_path / "level"
    path.write_text("WARN")
    log = Logger("app")
    watcher = LevelFileWatcher(log, path, poll_interval=0.05)
    watcher.start()
    watcher.start()  # idempotent
    deadline = time.time() + 5
    while log.level is not Level.WARN and time.time() < deadline:
        time.sleep(0.02)
    assert log.level is Level.WARN
    path.write_text("ERROR")
    deadline = time.time() + 5
    while log.level is not Level.ERROR and time.time() < deadline:
        time.sleep(0.02)
    assert log.level is Level.ERROR
    watcher.stop()
    watcher.stop()


@pytest.mark.skipif(not hasattr(signal, "SIGUSR1"), reason="needs POSIX signals")
def test_a_signal_rereads_the_variable_and_uninstalling_restores_the_old_handler(monkeypatch):
    log = Logger("app", level="INFO")
    before = signal.getsignal(signal.SIGUSR1)
    uninstall = install_signal_level_handler(log, env_var="MY_APP_LEVEL")
    monkeypatch.setenv("MY_APP_LEVEL", "debug")
    os.kill(os.getpid(), signal.SIGUSR1)
    deadline = time.time() + 5
    while log.level is not Level.DEBUG and time.time() < deadline:
        time.sleep(0.02)
    assert log.level is Level.DEBUG
    uninstall()
    assert signal.getsignal(signal.SIGUSR1) == before
