from __future__ import annotations

from abc import ABC, abstractmethod

from logquill.core.records import LogRecord
from logquill.formatters import Formatter, JSONFormatter


class Transport(ABC):
    """Sink for log records, per the cross-language transport contract:
    `format(record) -> str`, `write(formatted, record)`, `close()` on shutdown.
    """

    def __init__(self, formatter: Formatter | None = None) -> None:
        """`formatter` defaults to `JSONFormatter` — the canonical JSON line
        shape shared with logquill-js."""
        self.formatter: Formatter = formatter or JSONFormatter()

    def format(self, record: LogRecord) -> str:
        """Render `record` via this transport's configured `formatter`."""
        return self.formatter.format(record)

    @abstractmethod
    def write(self, formatted: str, record: LogRecord) -> None:
        """Send the already-formatted string (and the original `record`, for
        transports that need structured fields rather than the formatted
        text) to this transport's sink. Must not raise for a single bad
        record — see each concrete transport for its own failure handling."""
        ...

    def flush(self) -> None:  # noqa: B027 — intentionally optional to override
        """Push any internally buffered records out now, without releasing
        the transport's resources — see `BatchingTransport`, whose
        buffered-but-not-yet-sent batch this drains. No-op unless a
        transport overrides it. Called by `Logger.flush()`/`flush_async()`
        (e.g. from `with_lambda` before a serverless container may freeze),
        which — unlike `Logger.close()` — must not close anything a warm
        container will reuse on its next invocation.
        """

    def close(self) -> None:  # noqa: B027 — intentionally optional to override
        """Flush/release resources on shutdown. No-op unless a transport overrides it."""


class CollectingTransport(Transport):
    """In-memory transport for tests: collects every (formatted, record) pair written to it."""

    def __init__(self, formatter: Formatter | None = None) -> None:
        """Starts with empty `formatted`/`records` lists and `closed=False`."""
        super().__init__(formatter)
        self.formatted: list[str] = []
        self.records: list[LogRecord] = []
        self.closed = False

    def write(self, formatted: str, record: LogRecord) -> None:
        """Appends `formatted` and `record` to this transport's in-memory
        lists, for tests to assert against."""
        self.formatted.append(formatted)
        self.records.append(record)

    def close(self) -> None:
        """Marks this transport `closed`, for tests to assert shutdown ran."""
        self.closed = True
