"""The Python-only layer around the core: the worker, the stdlib bridge, toggle, config and the
serverless wrappers."""

import asyncio
import json
import logging
import os
import threading
import time

import pytest

import logquill
from logquill import (
    AsyncWorker,
    CollectingTransport,
    Logger,
    LogQuillHandler,
    disable,
    enable,
    load_config,
    logger_from_env,
    logger_from_file,
    with_azure_function,
    with_cloud_function,
    with_lambda,
)
from logquill.core.levels import Level
from logquill.runtime import toggle

# ---- AsyncWorker ------------------------------------------------------------------------------


def test_items_run_in_order_and_drain_waits_for_them():
    worker = AsyncWorker()
    out = []
    for i in range(50):
        worker.submit(lambda i=i: out.append(i))
    assert worker.drain(5) is True
    assert out == list(range(50)) and worker.qsize == 0
    worker.close()


def test_an_item_that_raises_does_not_stop_the_worker(capfd):
    worker = AsyncWorker()
    out = []
    worker.submit(lambda: 1 / 0)
    worker.submit(lambda: out.append("after"))
    assert worker.drain(5) and out == ["after"]
    assert "ZeroDivisionError" in capfd.readouterr().err
    worker.close()


def test_drop_oldest_keeps_the_newest_items(caplog):
    gate = threading.Event()
    worker = AsyncWorker(max_queue_size=3, backpressure="drop_oldest")
    out = []
    worker.submit(gate.wait)  # holds the thread so everything after queues up
    time.sleep(0.1)
    for i in range(10):
        worker.submit(lambda i=i: out.append(i))
    gate.set()
    assert worker.drain(5)
    assert out == [7, 8, 9]
    assert "queue full" in caplog.text
    worker.close()


def test_drop_newest_keeps_the_oldest_items():
    gate = threading.Event()
    worker = AsyncWorker(max_queue_size=3, backpressure="drop_newest")
    out = []
    worker.submit(gate.wait)
    time.sleep(0.1)
    for i in range(10):
        worker.submit(lambda i=i: out.append(i))
    gate.set()
    assert worker.drain(5) and out == [0, 1, 2]
    worker.close()


def test_block_makes_the_submitter_wait_and_loses_nothing():
    gate = threading.Event()
    worker = AsyncWorker(max_queue_size=2, backpressure="block")
    out = []
    worker.submit(gate.wait)
    time.sleep(0.1)

    def producer():
        for i in range(8):
            worker.submit(lambda i=i: out.append(i))

    thread = threading.Thread(target=producer)
    thread.start()
    time.sleep(0.3)
    assert thread.is_alive(), "the producer is waiting for room"
    gate.set()
    thread.join(10)
    assert worker.drain(5) and out == list(range(8))
    worker.close()


def test_drain_reports_a_timeout_and_close_is_idempotent_and_refuses_later_items():
    gate = threading.Event()
    worker = AsyncWorker()
    worker.submit(gate.wait)
    assert worker.drain(0.1) is False
    gate.set()
    assert worker.close(5) is True
    assert worker.close(5) is True
    worker.submit(lambda: 1 / 0)  # silently refused


def test_drain_async_and_validation():
    worker = AsyncWorker()
    worker.submit(lambda: None)
    assert asyncio.run(worker.drain_async(5)) is True
    worker.close()
    with pytest.raises(ValueError, match="max_queue_size"):
        AsyncWorker(max_queue_size=0)
    with pytest.raises(ValueError, match="backpressure"):
        AsyncWorker(backpressure="sideways")


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_the_worker_restarts_in_a_forked_child(tmp_path):
    worker = AsyncWorker()
    worker.submit(lambda: None)
    assert worker.drain(5)
    marker = tmp_path / "child"
    pid = os.fork()
    if pid == 0:
        try:
            worker.submit(lambda: marker.write_text("ran"))
            ok = worker.drain(10)
        finally:
            os._exit(0 if ok else 1)
    _, status = os.waitpid(pid, 0)
    assert os.WEXITSTATUS(status) == 0 and marker.read_text() == "ran"
    worker.close()


# ---- the stdlib logging bridge ----------------------------------------------------------------


def bridged(level=logging.NOTSET):
    sink = CollectingTransport()
    log = Logger("bridge", level="TRACE", transports=[sink], async_dispatch=False)
    stdlib = logging.getLogger(f"test.bridge.{id(sink)}")
    stdlib.setLevel(logging.DEBUG)
    stdlib.propagate = False
    handler = LogQuillHandler(log, level)
    stdlib.addHandler(handler)
    return stdlib, sink


def test_stdlib_calls_flow_through_with_levels_extra_fields_and_tracebacks():
    stdlib, sink = bridged()
    stdlib.debug("d")
    stdlib.info("hello %s", "world", extra={"attempt": 2})
    stdlib.warning("careful")
    stdlib.error("bad")
    stdlib.critical("worse")
    try:
        raise KeyError("missing")
    except KeyError:
        stdlib.exception("with traceback")
    levels = [(r["level"], r["message"]) for r in sink.records]
    assert levels == [
        ("DEBUG", "d"),
        ("INFO", "hello world"),
        ("WARN", "careful"),
        ("ERROR", "bad"),
        ("FATAL", "worse"),
        ("ERROR", "with traceback"),
    ]
    assert sink.records[1]["meta"] == {"attempt": 2}
    assert "KeyError: 'missing'" in sink.records[5]["meta"]["stack"]


def test_the_handlers_own_level_filters_before_logquill_sees_the_record():
    stdlib, sink = bridged(level=logging.WARNING)
    stdlib.info("dropped")
    stdlib.warning("kept")
    assert [r["message"] for r in sink.records] == ["kept"]


def test_logquills_own_diagnostics_are_not_fed_back_into_it():
    sink = CollectingTransport()
    log = Logger("bridge", transports=[sink], async_dispatch=False)
    root = logging.getLogger()
    handler = LogQuillHandler(log)
    root.addHandler(handler)
    try:
        logging.getLogger("logquill").warning("a diagnostic")
        logging.getLogger("logquill.sub").error("another")
        logging.getLogger("logquillish").warning("not ours")
    finally:
        root.removeHandler(handler)
    assert [r["message"] for r in sink.records] == ["not ours"]


# ---- disable / enable -------------------------------------------------------------------------


def test_disable_and_enable_follow_the_most_specific_rule():
    try:
        disable("mylib")
        enable("mylib.http")
        assert not toggle.is_enabled("mylib") and not toggle.is_enabled("mylib.db")
        assert toggle.is_enabled("mylib.http") and toggle.is_enabled("mylib.http.client")
        assert toggle.is_enabled("mylib2") and toggle.is_enabled("other")
        sink = CollectingTransport()
        Logger("mylib.db", transports=[sink], async_dispatch=False).info("silenced")
        Logger("mylib.http", transports=[sink], async_dispatch=False).info("heard")
        assert [r["message"] for r in sink.records] == ["heard"]
        disable("")
        enable("app")
        assert toggle.is_enabled("app.api") and not toggle.is_enabled("other")
    finally:
        toggle._reset()
    assert toggle.is_enabled("anything")


def test_disable_wants_a_logger_name():
    with pytest.raises(TypeError, match="logger name such as __name__, got int"):
        disable(5)  # type: ignore[arg-type]


# ---- config -----------------------------------------------------------------------------------


def test_load_config_builds_transports_plugins_and_options(tmp_path):
    log = load_config(
        {
            "name": "svc",
            "level": "debug",
            "transports": [
                {"type": "file", "options": {"path": str(tmp_path / "a.log"), "formatter": "logfmt"}},
                {"class": "logquill.transports.transport.CollectingTransport"},
            ],
            "plugins": [{"type": "redact"}, {"type": "context", "options": {"service": "api"}}],
            "max_queue_size": 50,
            "backpressure": "drop_newest",
        }
    )
    assert log.name == "svc" and log.level is Level.DEBUG and len(log.transports) == 2
    assert log._async is True, "queued by default (decision D2)"
    log.info("hello", password="p")
    log.close()
    line = (tmp_path / "a.log").read_text()
    assert "password=***" in line and "service=api" in line
    assert load_config({"async_dispatch": False})._async is False


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"transports": [{"options": {}}]}, "needs a 'type' or 'class' key"),
        ({"transports": [{"type": "carrier-pigeon"}]}, "Unknown type 'carrier-pigeon'"),
        ({"transports": [{"class": "NoDots"}]}, "fully-qualified dotted path"),
        ({"transports": [{"class": "logquill.transports.transport.Nope"}]}, "has no attribute 'Nope'"),
        ({"transports": [{"type": "console", "options": {"formatter": "xml"}}]}, "Unknown formatter 'xml'"),
        ({"level": "loud"}, "Unknown log level"),
    ],
)
def test_config_errors_say_what_to_change(config, message):
    with pytest.raises(ValueError, match=message):
        load_config(config)


def test_logger_from_file_and_env(tmp_path, monkeypatch):
    path = tmp_path / "log.json"
    path.write_text(json.dumps({"level": "WARN", "transports": [{"type": "console"}]}))
    assert logger_from_file(path).level is Level.WARN
    monkeypatch.setenv("LOGQUILL_CONFIG_FILE", str(path))
    assert logger_from_env().level is Level.WARN
    monkeypatch.setenv("LOGQUILL_LEVEL", "debug")
    assert logger_from_env().level is Level.DEBUG, "the variable wins over the file"
    monkeypatch.setenv("LOGQUILL_LEVEL", "30")
    assert logger_from_env().level is Level.WARN, "a weight works too"
    monkeypatch.delenv("LOGQUILL_CONFIG_FILE")
    monkeypatch.delenv("LOGQUILL_LEVEL")
    assert logger_from_env(name="x").name == "x"


# ---- serverless -------------------------------------------------------------------------------


@pytest.mark.parametrize("decorator", [with_lambda, with_cloud_function, with_azure_function])
def test_the_handler_wrapper_flushes_before_returning_even_when_it_raises(decorator):
    gate = threading.Event()

    class Slow(CollectingTransport):
        def write(self, formatted, record):
            gate.wait(5)
            super().write(formatted, record)

    sink = Slow()
    log = Logger("fn", transports=[sink])

    @decorator(log)
    def handler(event):
        log.info("handled", event=event)
        gate.set()
        return event * 2

    @decorator([log])
    def failing(event):
        log.info("about to fail")
        raise RuntimeError("boom")

    assert handler(21) == 42
    assert [r["message"] for r in sink.records] == ["handled"], "flushed before the result came back"
    with pytest.raises(RuntimeError):
        failing(1)
    assert [r["message"] for r in sink.records] == ["handled", "about to fail"]
    assert not sink.closed, "a warm container reuses the logger, so it is flushed and not closed"


def test_the_async_handler_wrapper_awaits_the_flush():
    sink = CollectingTransport()
    log = Logger("fn", transports=[sink])

    @with_lambda(log)
    async def handler():
        log.info("async handled")
        return "ok"

    assert asyncio.run(handler()) == "ok"
    assert [r["message"] for r in sink.records] == ["async handled"]


def test_the_package_exposes_its_version():
    assert logquill.__version__.startswith("3.0.0")
