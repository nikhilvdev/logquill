from __future__ import annotations

from typing import Any

from logquill.core.records import LogRecord
from logquill.plugins.plugin import Plugin


class ContextPlugin(Plugin):
    """Injects fixed key/value pairs into every record's `meta`.

    A value already present in a record's own `meta` wins over the fixed context.
    """

    def __init__(self, **context: Any) -> None:
        """`context` is the fixed set of key/value pairs injected into every
        record this plugin sees."""
        self.context = context

    def before_log(self, record: LogRecord) -> LogRecord | None:
        """Merges the fixed `context` under the record's own `meta`, so any
        key already present in `meta` is left untouched."""
        record["meta"] = {**self.context, **record["meta"]}
        return record
