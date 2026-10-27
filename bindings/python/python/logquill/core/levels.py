from __future__ import annotations

from enum import IntEnum


class Level(IntEnum):
    """Log levels, shared by name and numeric weight with the logquill-js contract."""

    TRACE = 5
    DEBUG = 10
    INFO = 20
    WARN = 30
    ERROR = 40
    FATAL = 50


_NAME_TO_LEVEL = {level.name: level for level in Level}

#: Names are matched after folding ASCII letters only (decision D35), so `"ınfo"` (a dotless i) is
#: not `INFO`, which is what Python's own `str.upper()` would have made it.
_ASCII_UPPER = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _json_type(value: object) -> str:
    """The JSON name of a value's type, as the cross-language messages give it."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def parse_level(level: int | str | Level) -> Level:
    """Normalize a level given as a `Level`, a level name in any case (ASCII letters), or the
    numeric weight of one of the six levels.

    Raises `ValueError("Unknown log level: <value>")` for a name or number that is not a level
    (`20.0` and `20.5` are not weights), and `TypeError` for anything that is neither a name nor a
    number, including `None`, a boolean, a list or a dict.
    """
    if isinstance(level, Level):
        return level
    if isinstance(level, str):
        try:
            return _NAME_TO_LEVEL[level.translate(_ASCII_UPPER)]
        except KeyError:
            raise ValueError(f"Unknown log level: {level}") from None
    if isinstance(level, bool):
        raise TypeError(f"level must be a name or a number, got {_json_type(level)}")
    if isinstance(level, (int, float)):
        try:
            return Level(level) if isinstance(level, int) else _unknown(level)
        except ValueError:
            raise ValueError(f"Unknown log level: {level}") from None
    raise TypeError(f"level must be a name or a number, got {_json_type(level)}")


def _unknown(level: float) -> Level:
    raise ValueError(f"Unknown log level: {level}")
