from __future__ import annotations

import pytest

from logquill.core.levels import Level, parse_level


def test_level_values() -> None:
    assert Level.TRACE == 5
    assert Level.DEBUG == 10
    assert Level.INFO == 20
    assert Level.WARN == 30
    assert Level.ERROR == 40
    assert Level.FATAL == 50


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("trace", Level.TRACE),
        ("INFO", Level.INFO),
        (30, Level.WARN),
        (Level.FATAL, Level.FATAL),
    ],
)
def test_parse_level(value: int | str | Level, expected: Level) -> None:
    assert parse_level(value) == expected


def test_parse_level_unknown_string() -> None:
    with pytest.raises(ValueError, match="Unknown log level"):
        parse_level("verbose")


def test_parse_level_unknown_int() -> None:
    with pytest.raises(ValueError, match="Unknown log level"):
        parse_level(999)


def test_parse_level_wrong_type() -> None:
    with pytest.raises(TypeError):
        parse_level(3.14)  # type: ignore[arg-type]
