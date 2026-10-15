from __future__ import annotations

import io
from pathlib import Path

from logquill import Logger, RunPlugin
from logquill.cli.cli import _CLEAR_SCREEN, _run_dev, build_parser
from logquill.transports.file_transport import FileTransport


def _logger_for(path: Path, run_id: str) -> Logger:
    return Logger("app.agent", transports=[FileTransport(path)], plugins=[RunPlugin(run_id=run_id)])


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def _dev(file: str, *extra_args: str, **kwargs: object) -> tuple[str, str]:
    args = build_parser().parse_args(["dev", file, *extra_args])
    out, warn = io.StringIO(), io.StringIO()
    _run_dev(args, out=out, warn_stream=warn, poll_interval=0, **kwargs)  # type: ignore[arg-type]
    return out.getvalue(), warn.getvalue()


def test_dev_renders_the_most_recently_active_run_on_startup(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    logger = _logger_for(path, "run-1")
    with logger.span("run"):
        logger.thought("plan")
    logger.close()

    output, warn = _dev(str(path), max_iterations=0)

    assert warn == ""
    assert "run run-1" in output
    assert "plan" in output


def test_a_missing_file_reports_a_helpful_error(tmp_path: Path) -> None:
    args = build_parser().parse_args(["dev", str(tmp_path / "missing.log")])
    out, warn = io.StringIO(), io.StringIO()

    exit_code = _run_dev(args, out=out, warn_stream=warn)

    assert exit_code == 1
    assert "no such file" in warn.getvalue()


def test_dev_redraws_when_new_records_for_the_tracked_run_arrive(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    logger = _logger_for(path, "run-1")
    logger.thought("first")

    args = build_parser().parse_args(["dev", str(path)])
    out = io.StringIO()

    def append_more(_: object = None) -> None:
        logger.action("second", tool="search")

    # patch time.sleep so the poll loop's single iteration does real work
    import logquill.cli.cli as cli_module

    original_sleep = cli_module.time.sleep
    cli_module.time.sleep = lambda _s: append_more()  # type: ignore[assignment]
    try:
        _run_dev(args, out=out, warn_stream=io.StringIO(), poll_interval=0, max_iterations=2)
    finally:
        cli_module.time.sleep = original_sleep

    renders = out.getvalue().split("logquill dev")
    assert len(renders) >= 2  # redrawn at least once beyond the initial render
    assert "second" in out.getvalue()


def test_dev_follows_a_new_run_once_the_old_one_finishes(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    first = _logger_for(path, "run-1")
    with first.span("run"):
        pass

    args = build_parser().parse_args(["dev", str(path)])
    out = io.StringIO()

    import logquill.cli.cli as cli_module

    second = _logger_for(path, "run-2")

    def switch(_: object = None) -> None:
        second.thought("new run started")

    original_sleep = cli_module.time.sleep
    cli_module.time.sleep = lambda _s: switch()  # type: ignore[assignment]
    try:
        _run_dev(args, out=out, warn_stream=io.StringIO(), poll_interval=0, max_iterations=2)
    finally:
        cli_module.time.sleep = original_sleep

    assert "run run-2" in out.getvalue()


def test_an_explicit_run_id_is_never_overridden_by_a_newer_run(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    first = _logger_for(path, "run-1")
    with first.span("run"):
        pass

    args = build_parser().parse_args(["dev", str(path), "--run-id", "run-1"])
    out = io.StringIO()

    import logquill.cli.cli as cli_module

    second = _logger_for(path, "run-2")

    def switch(_: object = None) -> None:
        second.thought("a different run")

    original_sleep = cli_module.time.sleep
    cli_module.time.sleep = lambda _s: switch()  # type: ignore[assignment]
    try:
        _run_dev(args, out=out, warn_stream=io.StringIO(), poll_interval=0, max_iterations=2)
    finally:
        cli_module.time.sleep = original_sleep

    assert "run run-1" in out.getvalue()
    assert "run-2" not in out.getvalue()


def test_backlog_caps_how_much_pre_existing_history_seeds_the_view(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    logger = _logger_for(path, "run-1")
    for i in range(10):
        logger.thought(f"step {i}")

    output, _warn = _dev(str(path), "--backlog", "3", max_iterations=0)

    assert "step 9" in output
    assert "step 6" not in output  # only the last 3 lines were kept


def test_no_run_at_all_yet_renders_nothing_but_does_not_crash(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_text(
        '{"message": "no run_id here", "level": "INFO", "meta": {}}\n', encoding="utf-8"
    )

    output, warn = _dev(str(path), max_iterations=0)

    assert output == ""
    assert warn == ""


def test_color_and_clear_screen_are_used_on_a_tty_and_suppressed_otherwise(
    tmp_path: Path,
) -> None:
    path = tmp_path / "app.log"
    logger = _logger_for(path, "run-1")
    with logger.span("run"):
        pass

    args = build_parser().parse_args(["dev", str(path)])
    plain_out = io.StringIO()
    _run_dev(args, out=plain_out, warn_stream=io.StringIO(), poll_interval=0, max_iterations=0)
    assert _CLEAR_SCREEN not in plain_out.getvalue()
    assert "\x1b[" not in plain_out.getvalue()

    tty_out = _FakeTTY()
    _run_dev(args, out=tty_out, warn_stream=io.StringIO(), poll_interval=0, max_iterations=0)
    assert _CLEAR_SCREEN in tty_out.getvalue()
    assert "\x1b[32m" in tty_out.getvalue()  # INFO green


def test_no_color_suppresses_color_and_clearing_even_on_a_tty(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    logger = _logger_for(path, "run-1")
    with logger.span("run"):
        pass

    args = build_parser().parse_args(["dev", str(path), "--no-color"])
    tty_out = _FakeTTY()

    _run_dev(args, out=tty_out, warn_stream=io.StringIO(), poll_interval=0, max_iterations=0)

    assert _CLEAR_SCREEN not in tty_out.getvalue()
