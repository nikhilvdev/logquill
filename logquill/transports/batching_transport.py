from __future__ import annotations

import json
import logging
from abc import abstractmethod
from typing import Generic, Sequence, TypeVar, cast

from logquill.core.records import LogRecord
from logquill.formatters import Formatter
from logquill.transports.transport import Transport

T = TypeVar("T")

_logger = logging.getLogger("logquill")


class BatchingTransport(Transport, Generic[T]):
    """Shared base for every transport that buffers records and sends them in
    batches (SQL, NoSQL, message queue, and cloud-native sinks).

    Bounds the buffer by **both** record count and estimated byte size —
    count alone lets a handful of huge `meta` payloads blow past reasonable
    memory before a batch triggers. A flush fires as soon as either bound is
    hit, checked after every `write()`. `_send_batch` is never called with an
    empty batch, and a failing send is caught and logged rather than
    propagated — a slow or down sink can never crash the caller's process.
    """

    def __init__(
        self,
        *,
        formatter: Formatter | None = None,
        max_records: int = 100,
        max_bytes: int = 1_000_000,
    ) -> None:
        """`max_records`/`max_bytes` are the two independent triggers for an
        automatic flush — whichever is hit first, checked after every
        `write()`."""
        super().__init__(formatter)
        self.max_records = max_records
        self.max_bytes = max_bytes
        self._buffer: list[T] = []
        self._buffer_bytes = 0

    def _to_item(self, formatted: str, record: LogRecord) -> T:
        return cast(T, record)

    def _size_of(self, item: T) -> int:
        return len(json.dumps(item, separators=(",", ":"), default=str).encode("utf-8"))

    def write(self, formatted: str, record: LogRecord) -> None:
        """Buffers `record` (converted via `_to_item`) and triggers a
        `flush()` as soon as either `max_records` or `max_bytes` is reached."""
        item = self._to_item(formatted, record)
        self._buffer.append(item)
        self._buffer_bytes += self._size_of(item)
        if len(self._buffer) >= self.max_records or self._buffer_bytes >= self.max_bytes:
            self.flush()

    def flush(self) -> None:
        """Sends whatever is currently buffered via `_send_batch`, clearing
        the buffer first so a failing send doesn't retry the same batch on
        the next flush; a send failure is caught and logged, never raised
        to the caller. No-op if nothing is buffered."""
        if not self._buffer:
            return
        batch, self._buffer = self._buffer, []
        self._buffer_bytes = 0
        try:
            self._send_batch(batch)
        except Exception:
            _logger.exception("%s: failed to send log batch", type(self).__name__)

    def close(self) -> None:
        """Flushes any remaining buffered records."""
        self.flush()

    @abstractmethod
    def _send_batch(self, batch: Sequence[T]) -> None:
        """Send one batch of buffered items to the concrete sink. Called
        only with a non-empty `batch`; a raised exception is caught by
        `flush()` and logged, not propagated."""
        ...
