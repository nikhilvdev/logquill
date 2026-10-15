from __future__ import annotations

from typing import Protocol

from logquill.core.records import LogRecord


class Formatter(Protocol):
    """`format(record) -> string`, per the transport contract shared with logquill-js."""

    def format(self, record: LogRecord) -> str:
        """Render `record` to the string a transport will write."""
        ...
