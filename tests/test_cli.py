from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from logquill.cli.cli import _follow, _read_existing, _run_tail, build_parser, main
from logquill.core.levels import Level


def _write_lines(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _tail(file: str, *extra_args: str, min_level: Level | None = None) -> tuple[str, str, int]:
    args = build_parser().parse_args(["tail", file, *extra_args])
    out, warn = io.StringIO(), io.StringIO()
    exit_code = _run_tail(args, min_level=min_level, out=out, warn_stream=warn)
    return out.getvalue(), warn.getvalue(), exit_code


def test_tail_prints_human_readable_lines_by_default(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    _write_lines(
        log_path,
        [
            {
                "timestamp": "2026-01-01T00:00:00.000Z",
                "level": "INFO",
                "logger": "app",
                "message": "started",
                "meta": {"pid": 1},
            }
        ],
    )

    output, _warn, exit_code = _tail(str(log_path))

    assert exit_code == 0
    assert "2026-01-01T00:00:00.000Z" in output
    assert "INFO" in output
    assert "app: started" in output
    assert '{"pid":1}' in output


def test_tail_json_flag_prints_raw_json_lines(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    record = {
        "timestamp": "2026-01-01T00:00:00.000Z",
        "level": "WARN",
        "logger": "app",
        "message": "careful",
        "meta": {},
    }
    _write_lines(log_path, [record])

    output, _warn, _exit_code = _tail(str(log_path), "--json")

    assert json.loads(output.strip()) == record


def test_tail_filters_by_level(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    _write_lines(
        log_path,
        [
            {"timestamp": "t", "level": "DEBUG", "logger": "app", "message": "quiet", "meta": {}},
            {"timestamp": "t", "level": "ERROR", "logger": "app", "message": "loud", "meta": {}},
        ],
    )

    output, _warn, _exit_code = _tail(str(log_path), "--level", "error", min_level=Level.ERROR)

    assert "loud" in output
    assert "quiet" not in output


def test_tail_lines_flag_limits_to_last_n_matching_records(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    _write_lines(
        log_path,
        [
            {"timestamp": "t", "level": "INFO", "logger": "app", "message": f"line-{i}", "meta": {}}
            for i in range(5)
        ],
    )

    output, _warn, _exit_code = _tail(str(log_path), "-n", "2")

    assert "line-3" in output
    assert "line-4" in output
    assert "line-0" not in output
    assert "line-1" not in output
    assert "line-2" not in output


def test_tail_skips_malformed_lines_and_warns(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    log_path.write_text(
        "not json\n"
        + json.dumps(
            {"timestamp": "t", "level": "INFO", "logger": "app", "message": "ok", "meta": {}}
        )
        + "\n"
        + json.dumps(["not", "an", "object"])
        + "\n",
        encoding="utf-8",
    )

    output, warn, exit_code = _tail(str(log_path))

    assert exit_code == 0
    assert "ok" in output
    assert "malformed" in warn
    assert "non-object" in warn


def test_tail_missing_file_returns_nonzero_and_warns(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.log"

    _output, warn, exit_code = _tail(str(missing))

    assert exit_code == 1
    assert "no such file" in warn


def test_tail_unknown_level_exits_with_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log_path = tmp_path / "app.log"
    log_path.write_text("", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        main(["tail", str(log_path), "--level", "nonsense"])

    assert exc_info.value.code == 2
    assert "Unknown log level" in capsys.readouterr().err


def test_tail_follow_picks_up_appended_records(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    _write_lines(
        log_path,
        [{"timestamp": "t", "level": "INFO", "logger": "app", "message": "first", "meta": {}}],
    )

    setup_out = io.StringIO()
    offset = _read_existing(
        log_path,
        min_level=None,
        lines=None,
        as_json=False,
        colorize=False,
        out=setup_out,
        warn_stream=io.StringIO(),
    )

    second = {"timestamp": "t", "level": "INFO", "logger": "app", "message": "second", "meta": {}}
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(second) + "\n")

    follow_out = io.StringIO()
    _follow(
        log_path,
        offset,
        min_level=None,
        as_json=False,
        colorize=False,
        out=follow_out,
        warn_stream=io.StringIO(),
        poll_interval=0.01,
        max_iterations=1,
    )

    assert "second" in follow_out.getvalue()
    assert "first" not in follow_out.getvalue()


def test_tail_prints_a_stack_trace_on_its_own_lines(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    _write_lines(
        log_path,
        [
            {
                "timestamp": "2026-01-01T00:00:00.000Z",
                "level": "ERROR",
                "logger": "app",
                "message": "failed",
                "meta": {"stack": "Traceback (most recent call last):\nValueError: nope\n"},
            }
        ],
    )

    output, _warn, _exit_code = _tail(str(log_path))

    assert output.splitlines()[1:] == ["Traceback (most recent call last):", "ValueError: nope"]
