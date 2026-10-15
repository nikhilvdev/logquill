from __future__ import annotations

import json

from logquill.core.records import LogRecord


class JSONFormatter:
    """Serializes a record to the canonical JSON line shape."""

    def format(self, record: LogRecord) -> str:
        """Serializes `record` to a single compact JSON line; non-JSON-native
        values fall back to `str()` rather than raising."""
        return json.dumps(record, separators=(",", ":"), default=str)
