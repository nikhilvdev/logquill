from __future__ import annotations

from typing import Sequence

from logquill.core.levels import Level
from logquill.core.records import LogRecord, create_record
from logquill.transports.batching_transport import BatchingTransport


class CollectingBatchTransport(BatchingTransport[LogRecord]):
    """Minimal concrete BatchingTransport for testing the shared base's
    buffering/flush logic in isolation from any real backend."""

    def __init__(self, *, max_records: int = 100, max_bytes: int = 1_000_000) -> None:
        super().__init__(max_records=max_records, max_bytes=max_bytes)
        self.sent_batches: list[list[LogRecord]] = []
        self.fail_next = False

    def _send_batch(self, batch: Sequence[LogRecord]) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("boom")
        self.sent_batches.append(list(batch))


def _record(message: str = "hello") -> LogRecord:
    return create_record(level=Level.INFO, logger="app.test", message=message, meta={})


def test_flushes_exactly_at_max_records() -> None:
    transport = CollectingBatchTransport(max_records=3, max_bytes=10_000_000)
    for _ in range(2):
        transport.write("x", _record())
    assert transport.sent_batches == []

    transport.write("x", _record())
    assert len(transport.sent_batches) == 1
    assert len(transport.sent_batches[0]) == 3


def test_max_bytes_flushes_immediately_even_with_huge_max_records() -> None:
    transport = CollectingBatchTransport(max_records=1000, max_bytes=1)
    transport.write("x", _record())
    assert len(transport.sent_batches) == 1


def test_close_flushes_a_partial_batch() -> None:
    transport = CollectingBatchTransport(max_records=100, max_bytes=10_000_000)
    transport.write("x", _record("only one"))
    transport.close()
    assert len(transport.sent_batches) == 1
    assert transport.sent_batches[0][0]["message"] == "only one"


def test_close_on_empty_buffer_sends_nothing() -> None:
    transport = CollectingBatchTransport()
    transport.close()
    assert transport.sent_batches == []


def test_failed_send_is_caught_and_does_not_raise() -> None:
    transport = CollectingBatchTransport(max_records=1)
    transport.fail_next = True
    transport.write("x", _record())  # must not raise
    assert transport.sent_batches == []


def test_buffer_is_cleared_before_send_avoiding_reentrant_double_send() -> None:
    transport = CollectingBatchTransport(max_records=1)
    transport.write("x", _record("first"))
    transport.write("x", _record("second"))
    assert len(transport.sent_batches) == 2
    assert transport.sent_batches[0][0]["message"] == "first"
    assert transport.sent_batches[1][0]["message"] == "second"
