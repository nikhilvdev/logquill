from __future__ import annotations

import logging
import threading
import time

import pytest

from logquill.runtime.worker import AsyncWorker

_POLL_TIMEOUT = 2.0
_POLL_INTERVAL = 0.005


def _wait_until(predicate: object, timeout: float = _POLL_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(_POLL_INTERVAL)
    return predicate()  # type: ignore[operator]


def test_rejects_invalid_max_queue_size() -> None:
    with pytest.raises(ValueError):
        AsyncWorker(max_queue_size=0)


def test_rejects_invalid_backpressure_policy() -> None:
    with pytest.raises(ValueError):
        AsyncWorker(backpressure="explode")  # type: ignore[arg-type]


def test_submitted_items_run_on_the_background_thread_and_drain_completes() -> None:
    worker = AsyncWorker()
    seen: list[int] = []
    for i in range(50):
        worker.submit(lambda i=i: seen.append(i))

    assert worker.drain(timeout=2.0) is True
    assert seen == list(range(50))
    worker.close()


def test_drain_below_the_limit_loses_nothing() -> None:
    # A burst *below* the configured queue limit must drain with zero
    # dropped records — only exceeding the limit should ever lose one.
    worker = AsyncWorker(max_queue_size=1000)
    lock = threading.Lock()
    seen: list[int] = []
    for i in range(500):
        worker.submit(lambda i=i: (lock.acquire(), seen.append(i), lock.release()))

    assert worker.close(timeout=5.0) is True
    assert sorted(seen) == list(range(500))


def test_drop_oldest_bounds_the_queue_and_keeps_the_newest_items() -> None:
    gate = threading.Event()
    started = threading.Event()
    worker = AsyncWorker(max_queue_size=10, backpressure="drop_oldest")
    lock = threading.Lock()
    seen: list[int] = []

    def item(i: int) -> None:
        if i == -1:
            started.set()
        gate.wait(timeout=5.0)
        with lock:
            seen.append(i)

    # Stall the very first item, and wait for the worker thread to actually
    # start running it (i.e. pop it off the queue), before firing the burst
    # below — otherwise it could itself be evicted as "oldest" before the
    # worker ever gets to it, which would make this test flaky.
    worker.submit(lambda: item(-1))
    assert _wait_until(started.is_set)
    for i in range(1000):
        worker.submit(lambda i=i: item(i))

    assert worker.qsize <= 10
    gate.set()
    assert worker.close(timeout=5.0) is True

    # The oldest queued items were evicted to make room, so only the most
    # recently submitted ones (plus the stalled first item) survive.
    assert -1 in seen
    assert len(seen) <= 12
    assert max(seen) == 999


def test_drop_newest_bounds_the_queue_and_keeps_the_oldest_items() -> None:
    gate = threading.Event()
    started = threading.Event()
    worker = AsyncWorker(max_queue_size=10, backpressure="drop_newest")
    lock = threading.Lock()
    seen: list[int] = []

    def item(i: int) -> None:
        if i == -1:
            started.set()
        gate.wait(timeout=5.0)
        with lock:
            seen.append(i)

    worker.submit(lambda: item(-1))
    assert _wait_until(started.is_set)
    for i in range(1000):
        worker.submit(lambda i=i: item(i))

    assert worker.qsize <= 10
    gate.set()
    assert worker.close(timeout=5.0) is True

    assert -1 in seen
    assert len(seen) <= 12
    # Later submissions were the ones discarded, so nothing near the tail
    # of the burst should have made it through.
    assert 999 not in seen


def test_block_backpressure_waits_for_space_and_drops_nothing() -> None:
    gate = threading.Event()
    worker = AsyncWorker(max_queue_size=5, backpressure="block")
    lock = threading.Lock()
    seen: list[int] = []

    def item(i: int) -> None:
        gate.wait(timeout=5.0)
        with lock:
            seen.append(i)

    def burst() -> None:
        for i in range(50):
            worker.submit(lambda i=i: item(i))

    burst_thread = threading.Thread(target=burst)
    burst_thread.start()

    # The submitting thread should be stalled, blocked on the full queue,
    # not silently dropping or racing ahead.
    assert not _wait_until(lambda: not burst_thread.is_alive(), timeout=0.3)

    gate.set()
    burst_thread.join(timeout=5.0)
    assert not burst_thread.is_alive()
    assert worker.close(timeout=5.0) is True
    assert sorted(seen) == list(range(50))


def test_drain_respects_timeout_when_a_stalled_item_never_finishes() -> None:
    gate = threading.Event()
    worker = AsyncWorker()
    worker.submit(lambda: gate.wait(timeout=5.0))

    assert worker.drain(timeout=0.1) is False

    gate.set()
    assert worker.drain(timeout=5.0) is True
    worker.close()


def test_a_raising_work_item_does_not_stop_the_worker() -> None:
    worker = AsyncWorker()
    ran_after = threading.Event()

    def boom() -> None:
        raise RuntimeError("boom")

    worker.submit(boom)
    worker.submit(ran_after.set)

    assert _wait_until(ran_after.is_set)
    assert worker.close(timeout=2.0) is True


def test_close_is_idempotent_and_drains_a_pending_backlog_first() -> None:
    worker = AsyncWorker()
    lock = threading.Lock()
    seen: list[int] = []
    for i in range(20):
        worker.submit(lambda i=i: (lock.acquire(), seen.append(i), lock.release()))

    assert worker.close(timeout=2.0) is True
    assert worker.close(timeout=1.0) is True
    assert sorted(seen) == list(range(20))


def test_submit_after_close_is_a_silent_no_op() -> None:
    worker = AsyncWorker()
    worker.close()
    ran = []
    worker.submit(lambda: ran.append(True))
    assert worker.qsize == 0
    assert ran == []


async def test_drain_async_awaits_completion_without_blocking_the_event_loop() -> None:
    worker = AsyncWorker()
    seen: list[int] = []
    for i in range(20):
        worker.submit(lambda i=i: seen.append(i))

    assert await worker.drain_async(timeout=2.0) is True
    assert sorted(seen) == list(range(20))
    worker.close()


def test_the_first_drop_warns_even_when_the_monotonic_clock_is_still_near_zero(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # the monotonic clock's zero point is arbitrary (boot time on many
    # platforms), so a process started shortly after boot must still warn
    monkeypatch.setattr("logquill.runtime.worker.time.monotonic", lambda: 5.0)
    gate = threading.Event()
    worker = AsyncWorker(max_queue_size=1, backpressure="drop_newest")

    with caplog.at_level(logging.WARNING, logger="logquill"):
        worker.submit(gate.wait)  # occupies the worker thread
        worker.submit(lambda: None)  # fills the queue
        worker.submit(lambda: None)  # dropped
    gate.set()
    worker.close()

    assert any("queue full" in record.getMessage() for record in caplog.records)
