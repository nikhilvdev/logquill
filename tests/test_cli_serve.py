from __future__ import annotations

import io
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from logquill import Logger
from logquill.cli.cli import _run_serve, build_parser
from logquill.transports.file_transport import FileTransport


def _write_a_log(path: Path) -> None:
    Logger("app", transports=[FileTransport(path)]).info("hello", run_id="run-1")


def test_file_and_db_are_mutually_exclusive_and_one_is_required() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["serve"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["serve", "--file", "a.log", "--db", "a.sqlite"])


def test_serve_reports_a_helpful_error_for_a_missing_file(tmp_path: Path) -> None:
    args = build_parser().parse_args(["serve", "--file", str(tmp_path / "missing.log")])
    out = io.StringIO()

    exit_code = _run_serve(args, out=out)

    assert exit_code == 1
    assert "no such file" in out.getvalue()


def test_serve_starts_a_real_server_and_prints_its_url(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    _write_a_log(path)
    args = build_parser().parse_args(["serve", "--file", str(path), "--port", "0"])
    out = io.StringIO()

    # `_run_serve` blocks in `serve_forever()` until Ctrl+C; run it on a
    # daemon thread so the test (and the process, if this thread never gets
    # to stop cleanly) doesn't hang on it.
    thread = threading.Thread(target=_run_serve, args=(args,), kwargs={"out": out}, daemon=True)
    thread.start()

    for _ in range(100):
        if "listening on" in out.getvalue():
            break
        time.sleep(0.02)
    assert "listening on" in out.getvalue()
    assert "1 run(s) found" in out.getvalue()

    url = out.getvalue().split("listening on ")[1].split(" ")[0]
    with urllib.request.urlopen(url, timeout=5) as response:
        assert response.status == 200
    with urllib.request.urlopen(url + "api/runs", timeout=5) as response:
        assert b"run-1" in response.read()


def test_serve_via_main_reports_the_error_for_a_bad_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from logquill.cli.cli import main

    exit_code = main(["serve", "--db", str(tmp_path / "missing.sqlite")])

    assert exit_code == 1
    assert "no such file" in capsys.readouterr().out
