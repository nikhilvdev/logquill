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


def parse_level(level: int | str | Level) -> Level:
    """Normalize a level given as a `Level`, level name, or numeric weight."""
    if isinstance(level, Level):
        return level
    if isinstance(level, str):
        try:
            return _NAME_TO_LEVEL[level.upper()]
        except KeyError:
            raise ValueError(f"Unknown log level: {level!r}") from None
    if isinstance(level, int):
        try:
            return Level(level)
        except ValueError:
            raise ValueError(f"Unknown log level: {level!r}") from None
    raise TypeError(f"level must be int, str, or Level, got {type(level)!r}")
