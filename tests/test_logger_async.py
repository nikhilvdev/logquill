from __future__ import annotations

import threading
import time
from typing import Any

from logquill.core.logger import Logger
from logquill.core.records import LogRecord
from logquill.transports.transport import CollectingTransport

_POLL_TIMEOUT = 2.0
_POLL_INTERVAL = 0.005


def _wait_until(predicate: Any, timeout: float = _POLL_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(_POLL_INTERVAL)
    return predicate()


class StallingTransport(CollectingTransport):
    """A `CollectingTransport` whose `write()` blocks until released — stands
    in for a slow/down sink so tests can assert the caller isn't the one
    waiting on it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()
        self.write_started = threading.Event()

    def write(self, formatted: str, record: LogRecord) -> None:
        self.write_started.set()
        self.gate.wait(timeout=5.0)
        super().write(formatted, record)


def test_sync_dispatch_is_synchronous_by_default() -> None:
    transport = StallingTransport()
    transport.gate.set()  # don't actually stall — just confirm default behavior
    logger = Logger("app.test", transports=[transport])

    logger.info("hello")

    assert len(transport.records) == 1


def test_async_dispatch_returns_before_the_transport_write_completes() -> None:
    transport = StallingTransport()
    logger = Logger("app.test", transports=[transport], async_dispatch=True)

    start = time.monotonic()
    record = logger.info("hello")
    elapsed = time.monotonic() - start

    assert record is not None
    assert elapsed < 1.0  # didn't block on the stalled transport
    assert transport.records == []

    transport.gate.set()
    assert logger.flush(timeout=2.0) is True
    assert len(transport.records) == 1


def test_close_drains_queued_records_before_returning() -> None:
    transport = StallingTransport()
    transport.gate.set()
    logger = Logger("app.test", transports=[transport], async_dispatch=True)

    for i in range(200):
        logger.info("msg", i=i)

    logger.close(timeout=5.0)
    assert len(transport.records) == 200


def test_flush_does_not_close_the_transport() -> None:
    transport = StallingTransport()
    transport.gate.set()
    logger = Logger("app.test", transports=[transport], async_dispatch=True)

    logger.info("hello")
    assert logger.flush(timeout=2.0) is True
    assert transport.closed is False

    logger.info("world")
    assert logger.flush(timeout=2.0) is True
    assert len(transport.records) == 2


async def test_flush_async_awaits_the_drain() -> None:
    transport = StallingTransport()
    logger = Logger("app.test", transports=[transport], async_dispatch=True)

    logger.info("hello")
    assert transport.records == []

    transport.gate.set()
    assert await logger.flush_async(timeout=2.0) is True
    assert len(transport.records) == 1


def test_child_logger_shares_the_parent_worker() -> None:
    transport = StallingTransport()
    transport.gate.set()
    parent = Logger("app", transports=[transport], async_dispatch=True)
    child = parent.child("child")

    parent.info("from parent")
    child.info("from child")

    assert parent.flush(timeout=2.0) is True
    assert len(transport.records) == 2


def test_drop_oldest_backpressure_bounds_memory_under_a_stalled_transport() -> None:
    transport = StallingTransport()
    logger = Logger(
        "app.test",
        transports=[transport],
        async_dispatch=True,
        max_queue_size=50,
        backpressure="drop_oldest",
    )

    logger.info("first")  # popped immediately, stalls the worker on `gate`
    assert _wait_until(transport.write_started.is_set)

    for i in range(5000):
        logger.info("burst", i=i)

    assert logger._worker is not None
    assert logger._worker.qsize <= 50

    transport.gate.set()
    logger.close(timeout=5.0)
    # Bounded: nowhere near 5001 records made it through, but the caller was
    # never blocked and the process never grew the queue past its limit.
    assert len(transport.records) <= 52
