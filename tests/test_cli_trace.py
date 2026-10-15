from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from logquill import Logger, RunPlugin
from logquill.cli.cli import _run_trace, build_parser, main
from logquill.transports.file_transport import FileTransport


def _write_a_run(path: Path, run_id: str = "run-1") -> None:
    logger = Logger(
        "app.agent", transports=[FileTransport(path)], plugins=[RunPlugin(run_id=run_id)]
    )
    with logger.span("run", operation="invoke_agent", agent_name="planner"):
        logger.thought("plan")
        with logger.span("step"):
            logger.llm_call("chat", model="m", tokens_in=10, tokens_out=5, cost_usd=0.02)
        logger.action("call", tool="search")
    logger.close()


def _trace(file: str, run_id: str, *extra_args: str) -> tuple[str, str, int]:
    args = build_parser().parse_args(["trace", run_id, "--file", file, *extra_args])
    out, warn = io.StringIO(), io.StringIO()
    exit_code = _run_trace(args, out=out, warn_stream=warn)
    return out.getvalue(), warn.getvalue(), exit_code


def test_trace_prints_an_indented_annotated_tree(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    _write_a_run(path)

    output, warn, exit_code = _trace(str(path), "run-1")

    assert exit_code == 0
    assert warn == ""
    assert "run  (" in output
    assert "10→5 tok" in output
    assert "$0.0200" in output
    assert "plan" in output and "call" in output


def test_trace_json_prints_a_nested_structure(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    _write_a_run(path)

    output, _warn, exit_code = _trace(str(path), "run-1", "--json")

    assert exit_code == 0
    (root,) = json.loads(output)
    assert root["record"]["message"] == "run"
    assert root["rollup"] == {"tokens_in": 10, "tokens_out": 5, "cost_usd": 0.02}
    messages = [child["record"]["message"] for child in root["children"]]
    assert messages == ["plan", "step", "call"]


def test_trace_ignores_records_from_other_runs_in_the_same_file(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    _write_a_run(path, run_id="run-1")
    _write_a_run(path, run_id="run-2")

    output, _warn, exit_code = _trace(str(path), "run-2")

    assert exit_code == 0
    assert output.count("run  (") == 1


def test_trace_reports_a_helpful_error_for_an_unknown_run_id(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    _write_a_run(path)

    output, warn, exit_code = _trace(str(path), "no-such-run")

    assert exit_code == 1
    assert output == ""
    assert "no-such-run" in warn
    assert str(path) in warn


def test_trace_reports_a_helpful_error_for_a_missing_file(tmp_path: Path) -> None:
    output, warn, exit_code = _trace(str(tmp_path / "missing.log"), "run-1")

    assert exit_code == 1
    assert "no such file" in warn


def test_trace_skips_malformed_lines_with_a_warning_but_still_traces(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    _write_a_run(path)
    with path.open("a") as f:
        f.write("not json at all\n")
        f.write('["a", "json", "array", "not", "an", "object"]\n')

    output, warn, exit_code = _trace(str(path), "run-1")

    assert exit_code == 0
    assert "run  (" in output
    assert "malformed JSON" in warn
    assert "non-object" in warn


def test_trace_via_main_entry_point(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "app.log"
    _write_a_run(path)

    exit_code = main(["trace", "run-1", "--file", str(path)])

    assert exit_code == 0
    assert "run  (" in capsys.readouterr().out


def test_the_run_id_is_positional_and_file_is_required(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["trace"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["trace", "run-1"])
