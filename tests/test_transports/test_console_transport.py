import io

from logquill.core.logger import Logger
from logquill.transports.console_transport import ConsoleTransport


def test_info_writes_to_stdout_uncolored_by_default_settings() -> None:
    out, err = io.StringIO(), io.StringIO()
    transport = ConsoleTransport(colorize=False, stdout=out, stderr=err)
    logger = Logger("app.test", transports=[transport])

    logger.info("hello")

    assert "hello" in out.getvalue()
    assert err.getvalue() == ""


def test_error_routes_to_stderr() -> None:
    out, err = io.StringIO(), io.StringIO()
    transport = ConsoleTransport(colorize=False, stdout=out, stderr=err)
    logger = Logger("app.test", transports=[transport])

    logger.error("boom")

    assert out.getvalue() == ""
    assert "boom" in err.getvalue()


def test_colorize_wraps_output_in_ansi_codes() -> None:
    out, err = io.StringIO(), io.StringIO()
    transport = ConsoleTransport(colorize=True, stdout=out, stderr=err)
    logger = Logger("app.test", transports=[transport])

    logger.info("hello")

    assert out.getvalue().startswith("\x1b[")
