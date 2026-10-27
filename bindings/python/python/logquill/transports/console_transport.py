from __future__ import annotations

import os
import sys
from typing import TextIO

from logquill import _native
from logquill.core.levels import Level, parse_level
from logquill.core.records import LogRecord
from logquill.formatters import Formatter, native_formatter_name
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


def colour_default(stream: TextIO | None = None) -> bool:
    """Colour only for a terminal, and never when `NO_COLOR` is set (decision D9)."""
    if os.environ.get("NO_COLOR"):
        return False
    stream = stream if stream is not None else sys.stdout
    try:
        return bool(stream.isatty())
    except Exception:
        return False


class ConsoleTransport(Transport):
    """Writes to stdout, routing ERROR/FATAL to stderr, colorized by level.

    Colour is on by default only when stdout is a terminal and `NO_COLOR` is unset; pass
    `colorize=True` or `False` to decide yourself. The work is done in Rust."""

    def __init__(
        self,
        *,
        formatter: Formatter | None = None,
        colorize: bool | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        """`stdout`/`stderr` are injectable (defaulting to whatever `sys.stdout`/`sys.stderr` are at
        the time of each write) so tests can capture output without redirecting the real streams."""
        super().__init__(formatter)
        self.colorize = colour_default(stdout) if colorize is None else colorize
        self._native = _native.console_transport(
            self.colorize, native_formatter_name(formatter) or "json", stdout, stderr
        )
        #: Handed to the Rust dispatcher when the formatter is one Rust can run itself.
        self._native_dispatch = self._native if native_formatter_name(formatter) else None

    def write(self, formatted: str, record: LogRecord) -> None:
        """Writes `formatted` to stdout, or stderr for `ERROR`/`FATAL`, colorized by level when
        `colorize` is set."""
        self._native.write(formatted, record)

    def _colorize(self, formatted: str, level: Level) -> str:
        color = _COLORS.get(parse_level(level))
        return formatted if color is None else f"{color}{formatted}{_RESET}"
