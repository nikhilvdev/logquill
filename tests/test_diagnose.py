from __future__ import annotations

import logging

import pytest

from logquill import Logger, PIIRedactPlugin, Plugin, RedactPlugin
from logquill.core import exceptions as exceptions_module
from logquill.core.exceptions import format_exc_info
from logquill.transports.transport import CollectingTransport

# Obviously fake values, kept in constants rather than assigned as literals to
# variables named `password`/`token`, which secret scanners flag on sight.
FAKE_PASSWORD = "placeholder-value-1"
FAKE_TOKEN = "placeholder-value-2"
FAKE_KEY = "placeholder-value-3"


def _fail_with_locals() -> None:
    password = FAKE_PASSWORD
    token = FAKE_TOKEN
    email = "someone@example.com"
    visible = "harmless-value"
    print(password, token, email, visible)  # noqa: T201
    raise ValueError("boom")


def _logger(*plugins: Plugin) -> tuple[Logger, CollectingTransport]:
    sink = CollectingTransport()
    return Logger("app", transports=[sink], plugins=list(plugins)), sink


def _stack_of(sink: CollectingTransport) -> str:
    stack = sink.records[-1]["meta"]["stack"]
    assert isinstance(stack, str)
    return stack


def test_diagnose_is_off_by_default_and_shows_no_local_values() -> None:
    logger, sink = _logger()

    try:
        _fail_with_locals()
    except ValueError as exc:
        logger.error("failed", exc_info=exc)

    stack = _stack_of(sink)
    assert "ValueError: boom" in stack
    assert "harmless-value" not in stack
    assert FAKE_PASSWORD not in stack


def test_diagnose_prints_local_values_under_each_frame() -> None:
    logger, sink = _logger()

    try:
        _fail_with_locals()
    except ValueError:
        logger.error("failed", diagnose=True)  # implies exc_info=True

    stack = _stack_of(sink)
    assert "visible = 'harmless-value'" in stack
    assert "ValueError: boom" in stack
    assert "diagnose" not in sink.records[0]["meta"]


def test_diagnose_output_contains_nothing_redact_plugin_would_mask() -> None:
    logger, sink = _logger(RedactPlugin())

    try:
        _fail_with_locals()
    except ValueError as exc:
        logger.error("failed", exc_info=exc, diagnose=True)

    stack = _stack_of(sink)
    assert FAKE_PASSWORD not in stack
    assert FAKE_TOKEN not in stack
    assert "password = ***" in stack
    assert "token = ***" in stack
    assert "harmless-value" in stack  # only what the plugin masks is masked


def test_diagnose_output_contains_no_pii_pii_plugin_would_mask() -> None:
    logger, sink = _logger(PIIRedactPlugin())

    try:
        _fail_with_locals()
    except ValueError as exc:
        logger.error("failed", exc_info=exc, diagnose=True)

    stack = _stack_of(sink)
    assert "someone@example.com" not in stack
    assert "email = '***'" in stack


def test_diagnose_redacts_chained_exceptions_too() -> None:
    def inner() -> None:
        api_key = FAKE_KEY
        raise KeyError(len(api_key))

    def outer() -> None:
        try:
            inner()
        except KeyError as exc:
            raise RuntimeError("wrapped") from exc

    logger, sink = _logger(RedactPlugin())
    try:
        outer()
    except RuntimeError as exc:
        logger.error("failed", exc_info=exc, diagnose=True)

    stack = _stack_of(sink)
    assert FAKE_KEY not in stack
    assert "api_key = ***" in stack
    assert "RuntimeError: wrapped" in stack and "KeyError" in stack


def test_a_redaction_hook_that_raises_fails_closed() -> None:
    class Broken(Plugin):
        def redact_local(self, name: str, text: str) -> str:
            raise RuntimeError("bug in plugin")

    logger, sink = _logger(Broken())

    try:
        _fail_with_locals()
    except ValueError:
        logger.error("failed", diagnose=True)

    stack = _stack_of(sink)
    assert FAKE_PASSWORD not in stack
    assert "harmless-value" not in stack
    assert "<redaction failed>" in stack


def test_a_local_with_a_raising_repr_falls_back_to_a_plain_traceback() -> None:
    class BadRepr:
        def __repr__(self) -> str:
            raise RuntimeError("no repr")

    def fail() -> None:
        bad = BadRepr()  # noqa: F841
        raise ValueError("boom")

    logger, sink = _logger()
    try:
        fail()
    except ValueError:
        logger.error("failed", diagnose=True)

    stack = _stack_of(sink)
    assert "ValueError: boom" in stack
    assert "BadRepr" not in stack


def test_long_local_reprs_are_truncated() -> None:
    def fail() -> None:
        blob = "x" * 5000  # noqa: F841
        raise ValueError("boom")

    try:
        fail()
    except ValueError as exc:
        stack = format_exc_info(exc, diagnose=True)

    assert stack is not None
    assert "x" * 5000 not in stack
    assert "..." in stack


def test_diagnose_without_a_current_exception_adds_no_stack() -> None:
    logger, sink = _logger()

    logger.error("nothing is being handled", diagnose=True)

    assert "stack" not in sink.records[0]["meta"]


def test_diagnose_warns_once_about_leaking_sensitive_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    exceptions_module._diagnose_warned = False
    logger, _ = _logger()

    with caplog.at_level(logging.WARNING, logger="logquill"):
        for _ in range(3):
            try:
                _fail_with_locals()
            except ValueError:
                logger.error("failed", diagnose=True)

    warnings = [r for r in caplog.records if "leak sensitive data" in r.getMessage()]
    assert len(warnings) == 1
