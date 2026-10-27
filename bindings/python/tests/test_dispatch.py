"""The Rust dispatcher and native transports behind `Logger`."""

import gc
import gzip
import io
import json
import os
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

import logquill
from logquill import (
    CollectingTransport,
    ConsoleTransport,
    FileTransport,
    HTTPTransport,
    JSONFormatter,
    Logger,
    LogfmtFormatter,
    TextFormatter,
    Transport,
)


class Slow(Transport):
    """A host transport that blocks in `write` until released."""

    def __init__(self):
        super().__init__()
        self.release = threading.Event()
        self.written = []
        self.events = []

    def write(self, formatted, record):
        self.release.wait(10)
        self.written.append(record["message"])

    def flush(self):
        self.events.append("flush")

    def close(self):
        self.events.append("close")


# ---- console -------------------------------------------------------------------------------


def test_console_routes_errors_to_stderr_and_does_not_colour_a_pipe_by_default():
    out, err = io.StringIO(), io.StringIO()
    log = Logger("app", transports=[ConsoleTransport(stdout=out, stderr=err)], async_dispatch=False)
    log.info("fine", a=1)
    log.error("bad")
    log.fatal("worse")
    assert [json.loads(line)["message"] for line in out.getvalue().splitlines()] == ["fine"]
    assert [json.loads(line)["message"] for line in err.getvalue().splitlines()] == ["bad", "worse"]
    assert "\x1b" not in out.getvalue() + err.getvalue()


def test_console_colour_follows_the_flag_and_the_formatter_is_honoured():
    out = io.StringIO()
    log = Logger(
        "app",
        transports=[ConsoleTransport(colorize=True, formatter=TextFormatter(), stdout=out)],
        async_dispatch=False,
    )
    log.warn("careful")
    assert out.getvalue().startswith("\x1b[33m") and out.getvalue().rstrip().endswith("\x1b[0m")
    assert "WARN  app: careful" in out.getvalue()


def test_console_writes_to_whatever_sys_stdout_is_at_the_time(capsys):
    log = Logger("app", transports=[ConsoleTransport()], async_dispatch=False)
    log.info("captured")
    assert json.loads(capsys.readouterr().out)["message"] == "captured"


def test_no_color_disables_the_default_even_on_a_terminal(monkeypatch):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    assert ConsoleTransport(stdout=Tty()).colorize is True
    monkeypatch.setenv("NO_COLOR", "1")
    assert ConsoleTransport(stdout=Tty()).colorize is False


# ---- file ----------------------------------------------------------------------------------


def test_file_transport_appends_rotates_and_keeps_the_backup_count(tmp_path):
    path = tmp_path / "logs" / "app.log"
    log = Logger("app", transports=[FileTransport(path, max_bytes=400, backup_count=2)])
    for i in range(60):
        log.info("record", i=i, pad="x" * 20)
    log.close()
    names = sorted(p.name for p in path.parent.iterdir())
    assert names == ["app.log", "app.log.1", "app.log.2"]
    assert all(json.loads(line)["logger"] == "app" for line in path.read_text().splitlines())


def test_file_transport_compresses_rotated_files(tmp_path):
    for kind in ("gz", "zip"):
        path = tmp_path / kind / "app.log"
        log = Logger("app", transports=[FileTransport(path, max_bytes=300, backup_count=1, compression=kind)])
        for i in range(30):
            log.info("record", i=i, pad="x" * 20)
        log.close()
        rotated = path.with_name(f"app.log.1.{kind}")
        assert rotated.exists()
        if kind == "gz":
            text = gzip.decompress(rotated.read_bytes()).decode()
        else:
            with zipfile.ZipFile(rotated) as archive:
                text = archive.read(archive.namelist()[0]).decode()
        assert json.loads(text.splitlines()[0])["message"] == "record"


def test_file_transport_encrypts_each_line_with_a_fernet_key(tmp_path):
    key = logquill.generate_fernet_key()
    path = tmp_path / "secret.log"
    log = Logger("app", transports=[FileTransport(path, encrypt_key=key)])
    log.info("top secret", user="alice")
    log.close()
    lines = path.read_text().splitlines()
    assert len(lines) == 1 and lines[0].startswith("gAAAA")
    assert "alice" not in path.read_text() and "secret" not in path.read_text()
    with pytest.raises(ValueError, match="Fernet key"):
        FileTransport(tmp_path / "x.log", encrypt_key="not-a-key")


def test_a_custom_formatter_is_run_in_python_but_written_by_rust(tmp_path):
    class Shout:
        def format(self, record):
            return record["message"].upper()

    path = tmp_path / "custom.log"
    log = Logger("app", transports=[FileTransport(path, formatter=Shout())])
    log.info("quiet")
    log.close()
    assert path.read_text() == "QUIET\n"


# ---- http ----------------------------------------------------------------------------------


def test_http_transport_posts_newline_delimited_batches(recorder):
    log = Logger("app", transports=[HTTPTransport(f"{recorder.base}/logs", batch_size=3)])
    for i in range(7):
        log.info("m", i=i)
    log.close()
    batches = [[json.loads(line)["meta"]["i"] for line in body.splitlines()] for body in recorder.bodies()]
    assert batches == [[0, 1, 2], [3, 4, 5], [6]]


def test_http_transport_with_a_custom_sender_keeps_the_python_path():
    sent = []
    transport = HTTPTransport("http://unused", batch_size=2, sender=lambda url, batch: sent.append(list(batch)))
    log = Logger("app", transports=[transport])
    for i in range(3):
        log.info("m", i=i)
    log.close()
    assert [len(batch) for batch in sent] == [2, 1]


def test_an_unreachable_http_endpoint_drops_the_batch_without_raising(caplog):
    log = Logger("app", transports=[HTTPTransport("http://127.0.0.1:1/x", batch_size=1, timeout=0.5)])
    log.info("lost")
    assert log.flush(10) is True
    log.close()
    assert "dropped" in caplog.text


# ---- host transports through the dispatcher -------------------------------------------------


@pytest.mark.parametrize("async_dispatch", [True, False])
def test_a_host_transport_sees_every_record_flush_and_close_in_order(async_dispatch):
    sink = CollectingTransport()
    log = Logger("app", transports=[sink], async_dispatch=async_dispatch)
    for i in range(50):
        log.info("m", i=i)
    log.flush(5)
    assert [r["meta"]["i"] for r in sink.records] == list(range(50))
    assert not sink.closed
    log.close()
    assert sink.closed


def test_a_transport_that_raises_does_not_stop_the_others_or_the_caller(caplog):
    class Broken(Transport):
        def write(self, formatted, record):
            raise RuntimeError("disk on fire")

    good = CollectingTransport()
    log = Logger("app", transports=[Broken(), good], async_dispatch=False)
    assert log.info("still works") is not None
    assert [r["message"] for r in good.records] == ["still works"]
    assert "disk on fire" in caplog.text


def test_a_formatter_that_raises_is_reported_for_that_record_only(caplog):
    class Picky:
        def format(self, record):
            if record["message"] == "bad":
                raise ValueError("cannot format this")
            return record["message"]

    sink = CollectingTransport(formatter=Picky())
    log = Logger("app", transports=[sink], async_dispatch=False)
    log.info("bad")
    log.info("good")
    assert sink.formatted == ["good"]
    assert "cannot format this" in caplog.text


def test_after_log_runs_after_the_write_and_only_for_plugins_that_define_it():
    seen = []
    sink = CollectingTransport()

    class After(logquill.Plugin):
        def after_log(self, record):
            seen.append((record["message"], len(sink.records)))

    log = Logger("app", transports=[sink], plugins=[After(), lambda r: r])
    log.info("one")
    log.flush(5)
    assert seen == [("one", 1)]
    assert len(log._after_plugins) == 1


# ---- the queue ----------------------------------------------------------------------------


def test_a_stalled_transport_drops_the_oldest_records_and_memory_stays_bounded(caplog):
    slow = Slow()
    log = Logger("app", transports=[slow], max_queue_size=50, backpressure="drop_oldest")
    for i in range(5000):
        log.info("m", i=i)
    stats = log._dispatcher.stats()
    assert stats["queued"] <= 50 and stats["dropped"] >= 4900
    slow.release.set()
    assert log.flush(10) is True
    log.close()
    assert len(slow.written) <= 52
    assert "queue full" in caplog.text


def test_drop_newest_keeps_the_first_records():
    slow = Slow()
    log = Logger("app", transports=[slow], max_queue_size=5, backpressure="drop_newest")
    for i in range(100):
        log.info(f"m{i}")
    time.sleep(0.1)  # let the worker take the first record and stall in it
    slow.release.set()
    log.close()
    assert slow.written[0] == "m0" and "m99" not in slow.written


def test_block_makes_the_caller_wait_and_loses_nothing():
    slow = Slow()
    log = Logger("app", transports=[slow], max_queue_size=2, backpressure="block")
    done = []

    def producer():
        for i in range(10):
            log.info(f"m{i}")
        done.append(True)

    thread = threading.Thread(target=producer)
    thread.start()
    time.sleep(0.3)
    assert not done, "the producer is waiting for room"
    slow.release.set()
    thread.join(10)
    log.close()
    assert slow.written == [f"m{i}" for i in range(10)]


def test_flush_reports_a_timeout_and_close_is_safe_to_repeat():
    slow = Slow()
    log = Logger("app", transports=[slow])
    log.info("stuck")
    assert log.flush(0.2) is False
    slow.release.set()
    assert log.flush(5) is True
    log.close()
    log.close()
    assert slow.events.count("close") == 1


def test_a_bad_queue_setting_says_what_to_change():
    with pytest.raises(ValueError, match="max_queue_size"):
        Logger("app", transports=[CollectingTransport()], max_queue_size=0)
    with pytest.raises(ValueError, match="backpressure"):
        Logger("app", transports=[CollectingTransport()], backpressure="sideways")


def test_a_logger_that_is_garbage_collected_writes_its_records_and_leaves_shared_transports_open():
    sink = CollectingTransport()
    keeper = Logger("keeper", transports=[sink])
    temporary = Logger("temporary", transports=[sink])
    for i in range(20):
        temporary.info("m", i=i)
    del temporary
    gc.collect()
    assert len(sink.records) == 20
    assert not sink.closed, "dropping one logger must not close a transport another still uses"
    keeper.info("still logging")
    keeper.close()
    assert sink.closed


def test_children_share_one_queue_and_the_parents_transports():
    sink = CollectingTransport()
    parent = Logger("app", transports=[sink])
    child = parent.child("db")
    assert child._dispatcher is parent._dispatcher and child.transports is parent.transports
    child.info("from child")
    parent.flush(5)
    assert [r["logger"] for r in sink.records] == ["app.db"]
    child.close()
    assert sink.closed


def test_flush_async_awaits_the_drain():
    import asyncio

    sink = CollectingTransport()
    log = Logger("app", transports=[sink])
    log.info("m")
    assert asyncio.run(log.flush_async(5)) is True
    assert len(sink.records) == 1


# ---- processes ----------------------------------------------------------------------------


def run_script(code, tmp_path):
    script = tmp_path / "script.py"
    script.write_text(code)
    done = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return done


def test_records_still_queued_when_the_interpreter_exits_are_written(tmp_path):
    log_file = tmp_path / "exit.log"
    run_script(
        f"""
from logquill import Logger, FileTransport
log = Logger("app", transports=[FileTransport({str(log_file)!r})])
for i in range(200):
    log.info("m", i=i)
# no flush, no close
""",
        tmp_path,
    )
    assert len(log_file.read_text().splitlines()) == 200


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_a_forked_child_can_log_through_the_parents_logger(tmp_path):
    log_file = tmp_path / "fork.log"
    log = Logger("app", transports=[FileTransport(log_file)])
    log.info("before fork")
    assert log.flush(5)
    pid = os.fork()
    if pid == 0:
        try:
            log.info("from child")
            ok = log.flush(10)
        finally:
            os._exit(0 if ok else 1)
    _, status = os.waitpid(pid, 0)
    assert os.WEXITSTATUS(status) == 0
    log.close()
    messages = [json.loads(line)["message"] for line in log_file.read_text().splitlines()]
    assert messages == ["before fork", "from child"]


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs fork")
def test_fork_flushes_queued_records_first(tmp_path):
    log_file = tmp_path / "prefork.log"
    log = Logger("app", transports=[FileTransport(log_file)])
    for i in range(500):
        log.info("queued", i=i)
    pid = os.fork()  # no manual flush: the at-fork hook drains the queue before the child exists
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    assert len(log_file.read_text().splitlines()) == 500
    log.close()
