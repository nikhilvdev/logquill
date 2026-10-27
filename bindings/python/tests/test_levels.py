"""Level parsing follows decision D35 of docs/spec/00-decisions.md."""

import pytest

from logquill import Level, Logger
from logquill.core.levels import parse_level


@pytest.mark.parametrize(
    ("given", "expected"),
    [("info", Level.INFO), ("INFO", Level.INFO), ("wArN", Level.WARN), (20, Level.INFO), (5, Level.TRACE), (Level.FATAL, Level.FATAL)],
)
def test_a_level_is_a_name_in_any_ascii_case_a_weight_or_a_member(given, expected):
    assert parse_level(given) is expected
    assert Logger("x", level=given).level is expected


def test_only_ascii_letters_are_folded():
    with pytest.raises(ValueError, match="Unknown log level: ınfo"):
        parse_level("ınfo")  # a dotless i; str.upper() would turn it into INFO


@pytest.mark.parametrize("given", ["loud", "", 25, 0, -1, 20.0, 20.5])
def test_an_unknown_name_or_number_says_so_showing_it_as_written(given):
    with pytest.raises(ValueError) as error:
        parse_level(given)
    assert str(error.value) == f"Unknown log level: {given}"


@pytest.mark.parametrize(
    ("given", "kind"),
    [(None, "null"), (True, "boolean"), (False, "boolean"), ([20], "array"), ({"level": 20}, "object")],
)
def test_anything_that_is_not_a_name_or_number_is_a_type_error(given, kind):
    with pytest.raises(TypeError, match=f"level must be a name or a number, got {kind}"):
        parse_level(given)


def test_set_level_accepts_the_same_inputs():
    log = Logger("x")
    log.set_level("error")
    assert log.level is Level.ERROR
    with pytest.raises(ValueError):
        log.set_level("loud")
