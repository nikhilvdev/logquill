from __future__ import annotations

from typing import Callable, Literal

from logquill import _native

BackpressurePolicy = Literal["drop_oldest", "drop_newest", "block"]

WorkItem = Callable[[], None]


def _warn(message: str) -> None:
    import logging

    logging.getLogger("logquill").warning("%s", message)


class AsyncWorker:
    """A bounded, in-memory queue and one background thread that runs submitted callables in order,
    in the Rust core. `submit()` returns to the caller without waiting on what the item does (say, a
    transport's blocking I/O), which is the point: a log call's caller never blocks on the sink. The
    thread restarts in a forked child, which a plain Python thread does not.

    `max_queue_size` bounds memory: a stalled or slow consumer (a down HTTP sink, a full disk) can't
    grow this queue without limit. `backpressure` decides what happens once that bound is hit:

    - `"drop_oldest"` (default) — evict the oldest queued item to make room for the new one. Favors
      recent records over old ones and never blocks the submitting thread.
    - `"drop_newest"` — discard the item just submitted instead. Favors records already queued over
      the newest one.
    - `"block"` — the submitting thread waits for space. Favors completeness over the non-blocking
      guarantee; only choose this if the caller can tolerate an occasional stall.

    Either drop policy logs at most one warning a minute while actively dropping, not one per item.
    """

    def __init__(
        self,
        *,
        max_queue_size: int = 10_000,
        backpressure: BackpressurePolicy = "drop_oldest",
    ) -> None:
        """Starts the background thread immediately: a worker is active as soon as it's constructed.
        Raises `ValueError` for a `max_queue_size` below 1 or an unknown `backpressure`."""
        self.max_queue_size = max_queue_size
        self.backpressure = backpressure
        self._worker = _native.Worker(max_queue_size, backpressure, _warn)

    @property
    def qsize(self) -> int:
        """Number of items currently queued (not yet started). Testing/introspection only."""
        return self._worker.queued()  # type: ignore[no-any-return]

    def submit(self, item: WorkItem) -> None:
        """Enqueue `item` for the background thread to run. Never blocks the caller unless
        `backpressure="block"` and the queue is full. Silently dropped if the worker has already been
        `close()`d. An item that raises is reported and does not stop the worker."""
        self._worker.submit(item)

    def drain(self, timeout: float | None = None) -> bool:
        """Block the calling thread until every item submitted so far has finished running, or
        `timeout` seconds elapse (`None` waits indefinitely). Returns whether the queue fully
        drained."""
        return self._worker.drain(timeout)  # type: ignore[no-any-return]

    async def drain_async(self, timeout: float | None = None) -> bool:
        """`asyncio`-friendly `drain()`: runs the blocking wait in an executor thread so it doesn't
        block the event loop while waiting."""
        # imported here, not at module level: `asyncio` costs ~12 ms to import
        import asyncio

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.drain, timeout)

    def close(self, timeout: float | None = 5.0) -> bool:
        """Drain, then stop the background thread. Idempotent. Returns whether the drain completed
        within `timeout` before shutdown."""
        return self._worker.close(timeout)  # type: ignore[no-any-return]
