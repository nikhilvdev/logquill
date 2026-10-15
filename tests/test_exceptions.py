from __future__ import annotations

import pytest

from logquill.core.exceptions import format_exc_info
from logquill.core.logger import Logger


def test_format_exc_info_from_exception_instance() -> None:
    try:
        raise ValueError("boom")
    except ValueError as exc:
        formatted = format_exc_info(exc)

    assert formatted is not None
    assert "ValueError: boom" in formatted
    assert "Traceback" in formatted


def test_format_exc_info_true_uses_active_exception() -> None:
    try:
        raise RuntimeError("active")
    except RuntimeError:
        formatted = format_exc_info(True)

    assert formatted is not None
    assert "RuntimeError: active" in formatted


def test_format_exc_info_true_outside_except_block_returns_none() -> None:
    assert format_exc_info(True) is None


def test_format_exc_info_falsy_returns_none() -> None:
    assert format_exc_info(None) is None
    assert format_exc_info(False) is None


def test_format_exc_info_tuple() -> None:
    try:
        raise KeyError("missing")
    except KeyError as exc:
        formatted = format_exc_info((type(exc), exc, exc.__traceback__))

    assert formatted is not None
    assert "KeyError" in formatted


def test_logger_error_with_exc_info_populates_stack_meta() -> None:
    logger = Logger("app.test")

    try:
        raise ValueError("boom")
    except ValueError as exc:
        record = logger.error("failed", exc_info=exc, user_id=42)

    assert record is not None
    assert "exc_info" not in record["meta"]
    assert record["meta"]["user_id"] == 42
    assert "ValueError: boom" in record["meta"]["stack"]


def test_logger_without_exc_info_has_no_stack_key() -> None:
    logger = Logger("app.test")
    record = logger.info("hello")

    assert record is not None
    assert "stack" not in record["meta"]


@pytest.mark.parametrize("falsy", [None, False])
def test_logger_falsy_exc_info_does_not_add_stack(falsy: object) -> None:
    logger = Logger("app.test")
    record = logger.error("failed", exc_info=falsy)

    assert record is not None
    assert "stack" not in record["meta"]
    assert "exc_info" not in record["meta"]
