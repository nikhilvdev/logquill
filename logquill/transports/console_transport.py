from __future__ import annotations

import sys
from typing import TextIO

from logquill.core.levels import Level, parse_level
from logquill.core.records import LogRecord
from logquill.formatters import Formatter
from logquill.transports.transport import Transport

_COLORS = {
    Level.TRACE: "\x1b[90m",  # gray
    Level.DEBUG: "\x1b[36m",  # cyan
    Level.INFO: "\x1b[32m",  # green
    Level.WARN: "\x1b[33m",  # yellow
    Level.ERROR: "\x1b[31m",  # red
    Level.FATAL: "\x1b[35m",  # magenta
}
_RESET = "\x1b[0m"


class ConsoleTransport(Transport):
    """Writes to stdout, routing ERROR/FATAL to stderr, colorized by level."""

    def __init__(
        self,
        *,
        formatter: Formatter | None = None,
        colorize: bool = True,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        """`stdout`/`stderr` are injectable (defaulting to `sys.stdout`/
        `sys.stderr`) so tests can capture output without redirecting the
        real streams."""
        super().__init__(formatter)
        self.colorize = colorize
        self._stdout: TextIO = stdout if stdout is not None else sys.stdout
        self._stderr: TextIO = stderr if stderr is not None else sys.stderr

    def write(self, formatted: str, record: LogRecord) -> None:
        """Writes `formatted` to stdout, or stderr for `ERROR`/`FATAL`,
        colorized by level when `colorize` is set."""
        level = parse_level(record["level"])
        stream = self._stderr if level >= Level.ERROR else self._stdout
        line = self._colorize(formatted, level) if self.colorize else formatted
        stream.write(line + "\n")
        stream.flush()

    def _colorize(self, formatted: str, level: Level) -> str:
        color = _COLORS.get(level)
        return formatted if color is None else f"{color}{formatted}{_RESET}"
