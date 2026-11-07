"""The `logquill` command that ships in the wheel, driven as a user would run it.

2.0.0 tested its pure-Python CLI by calling `_run_tail`, `_run_verify`, `TraceBuilder` and the web-UI helpers
directly (`test_cli*.py`, `test_trace_tree.py`, `test_webui.py`). Those internals are gone: the command is the Rust
program in `crates/logquill-cli`. This file keeps the part only Python can check, which is that logs written by the
Python `Logger` are read correctly by the command, and the entry points. The command's own behaviour is tested in
Rust (`crates/logquill-cli/tests/{commands,verify,viewer}.rs`), and the trace tree by the shared corpus group
`schema/corpus/trace-tree.json`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from logquill import AuditLogger, FileTransport, Logger, RunPlugin
from logquill.cli import main


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "logquill.cli", *args], capture_output=True, text=True, timeout=60, check=False
    )


def _write_a_run(path: Path, run_id: str = "run-1") -> None:
    logger = Logger("app.agent", transports=[FileTransport(path)], plugins=[RunPlugin(run_id=run_id)])
    with logger.span("run", operation="invoke_agent", agent_name="planner"):
        logger.thought("plan")
        with logger.span("step"):
            logger.llm_call("chat", model="m", tokens_in=10, tokens_out=5, cost_usd=0.02)
        logger.action("call", tool="search")
    logger.error("something broke")
    logger.close()


def _write_an_audit_log(path: Path) -> None:
    logger = AuditLogger("audit", transports=[FileTransport(path)])
    logger.info("login", user_id=1)
    logger.info("logout", user_id=1)
    logger.close()


@contextmanager
def _live(*args: str) -> Iterator[subprocess.Popen[str]]:
    """A long-running command (`tail -f`, `dev`, `serve`), stopped when the block ends."""
    process = subprocess.Popen(
        [sys.executable, "-m", "logquill.cli", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        yield process
    finally:
        process.terminate()
        process.communicate(timeout=30)


def _read_until(process: subprocess.Popen[str], needle: str, seconds: float = 20) -> str:
    """Read the command's output line by line until `needle` appears, so a slow start fails with a message."""
    assert process.stdout is not None
    seen = ""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if not line:
            break
        seen += line
        if needle in line:
            return seen
    raise AssertionError(f"{needle!r} never appeared in the output:\n{seen}")


# --- entry points -------------------------------------------------------------------------------------


def test_console_script_is_registered() -> None:
    scripts = {e.name: e.value for e in entry_points(group="console_scripts")}
    assert scripts.get("logquill") == "logquill.cli:main"


def test_main_returns_the_exit_code_instead_of_exiting() -> None:
    assert main(["--version"]) == 0
    assert main(["verify", "no-such-file.log"]) == 1


def test_a_usage_error_exits_with_code_2() -> None:
    assert _run("no-such-command").returncode == 2


# --- tail ---------------------------------------------------------------------------------------------


def test_tail_prints_human_readable_lines_by_default(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    _write_a_run(log)

    result = _run("tail", str(log), "--no-color")

    assert result.returncode == 0
    assert " INFO  app.agent: plan " in result.stdout  # the level column is padded to five characters
    assert " ERROR app.agent: something broke " in result.stdout


def test_tail_json_prints_the_raw_records(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    _write_a_run(log)

    result = _run("tail", str(log), "--json")

    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert [r["message"] for r in records] == ["plan", "chat", "step", "call", "run", "something broke"]


def test_tail_filters_by_level_and_limits_to_the_last_n(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    _write_a_run(log)

    errors = _run("tail", str(log), "--level=error", "--json")
    last_two = _run("tail", str(log), "-n", "2", "--json")

    assert [json.loads(line)["message"] for line in errors.stdout.splitlines()] == ["something broke"]
    assert [json.loads(line)["message"] for line in last_two.stdout.splitlines()] == ["run", "something broke"]


def test_tail_skips_a_malformed_line_and_says_so(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    _write_a_run(log)
    with log.open("a") as handle:
        handle.write("not json\n")

    result = _run("tail", str(log), "--json")

    assert result.returncode == 0
    assert len(result.stdout.splitlines()) == 6
    assert "not json" not in result.stdout
    assert result.stderr.strip() != ""


def test_tail_of_a_missing_file_fails_and_names_the_file(tmp_path: Path) -> None:
    result = _run("tail", str(tmp_path / "missing.log"))

    assert result.returncode == 1
    assert "missing.log" in result.stderr


def test_tail_rejects_an_unknown_level(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    _write_a_run(log)

    result = _run("tail", str(log), "--level=loud")

    assert result.returncode == 2
    assert "loud" in result.stderr


def test_tail_prints_a_stack_trace_on_its_own_lines(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    logger = Logger("app", transports=[FileTransport(log)])
    try:
        raise ValueError("boom")
    except ValueError as error:
        logger.error("failed", exc_info=error)
    logger.close()

    result = _run("tail", str(log), "--no-color")

    assert "ValueError: boom" in result.stdout
    assert "Traceback" in result.stdout
    assert len(result.stdout.splitlines()) > 2


def test_tail_follow_prints_records_appended_after_it_started(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    _write_a_run(log)

    with _live("tail", str(log), "-f", "--no-color") as process:
        _read_until(process, "something broke")
        logger = Logger("app.late", transports=[FileTransport(log)])
        logger.warn("arrived later")
        logger.close()
        _read_until(process, "arrived later")


# --- trace --------------------------------------------------------------------------------------------


def test_trace_prints_an_indented_annotated_tree(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    _write_a_run(log)

    result = _run("trace", "run-1", "--file", str(log), "--no-color")

    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert lines[0].startswith("├─ [INFO] run")
    assert any("[INFO] step" in line and "10→5 tok" in line and "$0.0200" in line for line in lines)
    assert any(line.startswith("│  │  └─ [INFO] chat") for line in lines)
    assert lines[-1].startswith("└─ [ERROR] something broke")


def test_trace_json_is_a_nested_structure(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    _write_a_run(log)

    result = _run("trace", "run-1", "--file", str(log), "--json")

    roots = json.loads(result.stdout)
    run = next(node for node in roots if node["record"]["message"] == "run")
    step = next(node for node in run["children"] if node["record"]["message"] == "step")
    assert step["children"][0]["record"]["message"] == "chat"


def test_trace_ignores_records_of_other_runs_in_the_same_file(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    _write_a_run(log, "run-1")
    _write_a_run(log, "run-2")

    result = _run("trace", "run-2", "--file", str(log), "--no-color")

    assert result.stdout.count("[ERROR] something broke") == 1
    assert result.stdout.count("[INFO] run") == 1


def test_trace_of_an_unknown_run_explains_what_to_check(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    _write_a_run(log)

    result = _run("trace", "nope", "--file", str(log))

    assert result.returncode == 1
    assert "nope" in result.stderr and "RunPlugin" in result.stderr


def test_trace_of_a_missing_file_fails(tmp_path: Path) -> None:
    result = _run("trace", "run-1", "--file", str(tmp_path / "missing.log"))

    assert result.returncode == 1
    assert "missing.log" in result.stderr


def test_trace_skips_a_malformed_line_but_still_traces(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    _write_a_run(log)
    with log.open("a") as handle:
        handle.write("not json\n")

    result = _run("trace", "run-1", "--file", str(log), "--no-color")

    assert result.returncode == 0
    assert "[INFO] run" in result.stdout
    assert result.stderr.strip() != ""


def test_trace_needs_a_run_id_and_a_file() -> None:
    assert _run("trace", "--file", "x.log").returncode == 2
    assert _run("trace", "run-1").returncode == 2


# --- dev ----------------------------------------------------------------------------------------------


def test_dev_draws_the_most_recent_run_on_startup(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    _write_a_run(log)

    with _live("dev", str(log), "--no-color") as process:
        drawn = _read_until(process, "something broke")

    assert "run run-1" in drawn
    assert "\x1b" not in drawn


def test_dev_of_a_missing_file_fails(tmp_path: Path) -> None:
    result = _run("dev", str(tmp_path / "missing.log"))

    assert result.returncode == 1
    assert "missing.log" in result.stderr


# --- serve --------------------------------------------------------------------------------------------


def test_serve_needs_exactly_one_source() -> None:
    assert _run("serve").returncode == 2
    assert _run("serve", "--file", "a.log", "--db", "b.sqlite").returncode == 2


def test_serve_of_a_missing_file_fails(tmp_path: Path) -> None:
    result = _run("serve", "--file", str(tmp_path / "missing.log"))

    assert result.returncode == 1
    assert "missing.log" in result.stderr


def test_serve_answers_the_page_and_lists_the_run(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    _write_a_run(log)

    with _live("serve", "--file", str(log), "--port", "0") as process:
        url = next(
            word for word in _read_until(process, "http://").split() if word.startswith("http://")
        ).rstrip("/")
        with urllib.request.urlopen(f"{url}/", timeout=10) as page:
            assert page.status == 200 and b"<html" in page.read().lower()
        with urllib.request.urlopen(f"{url}/api/runs", timeout=10) as response:
            (run,) = json.load(response)

    assert run["run_id"] == "run-1"
    assert (run["tokens_in"], run["tokens_out"], run["error_count"]) == (10, 5, 1)
    assert run["agent_name"] == "planner"


# --- verify -------------------------------------------------------------------------------------------


def test_verify_passes_on_an_untampered_log(tmp_path: Path) -> None:
    log = tmp_path / "audit.log"
    _write_an_audit_log(log)

    result = _run("verify", str(log))

    assert result.returncode == 0
    assert "OK" in result.stdout and "2 record(s)" in result.stdout and "chain head is" in result.stdout


def test_verify_fails_with_an_actionable_message_on_an_edited_line(tmp_path: Path) -> None:
    log = tmp_path / "audit.log"
    _write_an_audit_log(log)
    lines = log.read_text().splitlines()
    lines[0] = lines[0].replace("login", "EDITED")
    log.write_text("\n".join(lines) + "\n")

    result = _run("verify", str(log))

    assert result.returncode == 1
    assert "FAILED" in result.stdout
    assert "edited after being chained" in result.stdout
    assert "record 1" in result.stdout


def test_verify_of_a_missing_file_fails(tmp_path: Path) -> None:
    result = _run("verify", str(tmp_path / "missing.log"))

    assert result.returncode == 1
    assert "missing.log" in result.stdout


def _signed_log(path: Path) -> tuple[str, str]:
    """Write an audit log and return the hex key and the signature of its chain head."""
    logger = AuditLogger("audit", transports=[FileTransport(path)])
    logger.info("login", user_id=1)
    logger.info("logout", user_id=1)
    logger.flush()
    signature = logger.sign_head(b"key")
    logger.close()
    return b"key".hex(), signature


def test_verify_with_the_right_signature_passes(tmp_path: Path) -> None:
    log = tmp_path / "audit.log"
    key, signature = _signed_log(log)

    assert _run("verify", str(log), "--sign-key", key, "--signature", signature).returncode == 0


def test_verify_with_the_wrong_signature_fails(tmp_path: Path) -> None:
    log = tmp_path / "audit.log"
    key, _ = _signed_log(log)

    result = _run("verify", str(log), "--sign-key", key, "--signature", "0" * 64)

    assert result.returncode == 1 and "FAILED" in result.stdout


def test_verify_catches_a_truncated_file_when_the_head_was_signed(tmp_path: Path) -> None:
    log = tmp_path / "audit.log"
    key, signature = _signed_log(log)
    log.write_text(log.read_text().splitlines()[0] + "\n")

    assert _run("verify", str(log)).returncode == 0  # a shorter chain is still self-consistent
    assert _run("verify", str(log), "--sign-key", key, "--signature", signature).returncode == 1


def test_sign_key_and_signature_must_be_given_together(tmp_path: Path) -> None:
    log = tmp_path / "audit.log"
    _write_an_audit_log(log)

    result = _run("verify", str(log), "--sign-key", "00")

    assert result.returncode == 1
    assert "must be given together" in result.stdout


def test_a_sign_key_that_is_not_hex_is_rejected(tmp_path: Path) -> None:
    log = tmp_path / "audit.log"
    _write_an_audit_log(log)

    result = _run("verify", str(log), "--sign-key", "zz", "--signature", "ab")

    assert result.returncode == 1
    assert "hex" in result.stdout


@pytest.mark.parametrize("content", ["", '{"message": "no hash here", "meta": {}}\n'])
def test_verify_of_a_file_with_no_chained_records_does_not_crash(tmp_path: Path, content: str) -> None:
    log = tmp_path / "plain.log"
    log.write_text(content)

    result = _run("verify", str(log))

    assert result.returncode in (0, 1)
    assert "Traceback" not in result.stderr
