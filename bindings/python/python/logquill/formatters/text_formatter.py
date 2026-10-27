from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from logquill import _native
from logquill.core.records import LogRecord


def format_text(record: Mapping[str, Any]) -> str:
    """Render a record as one human-readable entry:

        2026-01-01T00:00:00.000Z INFO  app.api: user signed up {"user_id":42}

    Tolerant of partial records (a missing field renders as `?`). A formatted traceback in
    `meta["stack"]` is printed on the lines after the entry, as a traceback normally reads.
    """
    return _native.format_text(record)


class TextFormatter:
    """Human-readable single-entry output for local development and terminals,
    `ConsoleTransport(formatter=TextFormatter())`. Use `JSONFormatter` (the default) for anything
    a machine will read. It never raises on odd `meta` values; they degrade to their `str()`."""

    def format(self, record: LogRecord) -> str:
        """Renders `record` via `format_text`."""
        return format_text(record)
