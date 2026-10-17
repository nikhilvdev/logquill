from __future__ import annotations

import threading
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


def test_concurrent_writes_from_many_threads_lose_or_duplicate_nothing() -> None:
    """`async_dispatch=False` runs transport writes on the caller's own
    thread, so a transport shared by a `Logger` used from multiple
    application threads sees concurrent `write()` calls directly — this is
    the scenario `_buffer_lock` exists for."""
    transport = CollectingBatchTransport(max_records=7, max_bytes=10_000_000)
    writes_per_thread = 200
    thread_count = 16

    def hammer(thread_index: int) -> None:
        for i in range(writes_per_thread):
            transport.write("x", _record(f"t{thread_index}-{i}"))

    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    transport.flush()

    seen = [record["message"] for batch in transport.sent_batches for record in batch]
    assert len(seen) == writes_per_thread * thread_count
    assert len(set(seen)) == len(seen)  # no duplicates
    assert transport._buffer == []
    assert transport._buffer_bytes == 0
