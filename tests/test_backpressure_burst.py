"""A burst of tens of thousands of log calls against a transport that is
stalled the whole time: each backpressure policy must do exactly what it
promises, and the queue must never grow past its bound."""

from __future__ import annotations

import logging
import threading
import time

import pytest

from logquill import Logger
from logquill.core.records import LogRecord
from logquill.transports.transport import CollectingTransport

BURST = 30_000
QUEUE_LIMIT = 100


class _Stalled(CollectingTransport):
    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()
        self.first_write_started = threading.Event()

    def write(self, formatted: str, record: LogRecord) -> None:
        self.first_write_started.set()
        self.gate.wait(timeout=30)
        super().write(formatted, record)

    def delivered_indices(self) -> list[int]:
        return [int(record["meta"]["i"]) for record in self.records]


def _stalled_logger(policy: str) -> tuple[Logger, _Stalled]:
    transport = _Stalled()
    logger = Logger(
        "app.burst",
        transports=[transport],
        async_dispatch=True,
        max_queue_size=QUEUE_LIMIT,
        backpressure=policy,  # type: ignore[arg-type]
        flush_at_exit=False,
    )
    logger.info("first", i=0)  # the worker picks this up and stalls inside write()
    assert transport.first_write_started.wait(timeout=5)
    return logger, transport


def _burst(logger: Logger) -> None:
    for i in range(1, BURST):
        logger.info("burst", i=i)


def test_drop_oldest_keeps_the_newest_records_and_never_blocks_the_caller(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger, transport = _stalled_logger("drop_oldest")

    with caplog.at_level(logging.WARNING, logger="logquill"):
        started = time.monotonic()
        _burst(logger)  # would hang here if the caller ever blocked on the stalled sink
        elapsed = time.monotonic() - started

    assert logger._worker is not None
    assert logger._worker.qsize == QUEUE_LIMIT
    transport.gate.set()
    logger.close(timeout=10)

    # the in-flight record, then exactly the newest QUEUE_LIMIT — in order
    assert transport.delivered_indices() == [0, *range(BURST - QUEUE_LIMIT, BURST)]
    assert elapsed < 20
    assert len([r for r in caplog.records if "queue full" in r.getMessage()]) == 1


def test_drop_newest_keeps_the_oldest_records_and_never_blocks_the_caller() -> None:
    logger, transport = _stalled_logger("drop_newest")

    _burst(logger)

    assert logger._worker is not None
    assert logger._worker.qsize == QUEUE_LIMIT
    transport.gate.set()
    logger.close(timeout=10)

    assert transport.delivered_indices() == [0, *range(1, QUEUE_LIMIT + 1)]


def test_block_makes_the_caller_wait_and_then_delivers_every_record_in_order() -> None:
    logger, transport = _stalled_logger("block")
    burst = threading.Thread(target=_burst, args=(logger,), daemon=True)

    burst.start()
    burst.join(timeout=0.5)

    assert burst.is_alive(), "the caller should be blocked on the full queue, not racing ahead"
    assert logger._worker is not None
    assert logger._worker.qsize == QUEUE_LIMIT

    transport.gate.set()
    burst.join(timeout=30)
    logger.close(timeout=30)

    assert not burst.is_alive()
    assert transport.delivered_indices() == list(range(BURST))


@pytest.mark.parametrize("policy", ["drop_oldest", "drop_newest"])
def test_a_burst_below_the_limit_loses_nothing(policy: str) -> None:
    transport = CollectingTransport()
    logger = Logger(
        "app.burst",
        transports=[transport],
        async_dispatch=True,
        max_queue_size=QUEUE_LIMIT,
        backpressure=policy,  # type: ignore[arg-type]
        flush_at_exit=False,
    )

    for i in range(QUEUE_LIMIT):
        logger.info("burst", i=i)
    logger.close(timeout=10)

    assert len(transport.records) == QUEUE_LIMIT
