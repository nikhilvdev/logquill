from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from typing import Callable, Literal

_logger = logging.getLogger("logquill")

BackpressurePolicy = Literal["drop_oldest", "drop_newest", "block"]
_VALID_POLICIES = ("drop_oldest", "drop_newest", "block")

WorkItem = Callable[[], None]

#: Don't re-emit the "queue full, dropping records" warning on every single
#: drop — a stalled consumer under a sustained burst would otherwise flood
#: whatever's watching this logger's own diagnostic channel with thousands
#: of near-identical lines. Once per minute is enough to notice the
#: condition without becoming the next noisy-logging problem.
_DROP_WARNING_INTERVAL_SECONDS = 60.0


class AsyncWorker:
    """Background-thread dispatch queue backing `Logger(async_dispatch=True)`.

    A single daemon thread pulls submitted work items (closures) off a
    bounded, in-memory queue and runs them one at a time, so `submit()`
    returns to the caller without waiting on whatever the item actually
    does (e.g. a transport's blocking I/O) — that's the whole point: a log
    call's caller never blocks on the sink.

    `max_queue_size` bounds memory: a stalled or slow consumer (a down
    HTTP sink, a full disk) can't grow this queue without limit. `backpressure`
    decides what happens once that bound is hit:

    - `"drop_oldest"` (default) — evict the oldest queued item to make room
      for the new one. Favors recent records over old ones and never blocks
      the submitting thread.
    - `"drop_newest"` — discard the item just submitted instead. Favors
      records already queued over the newest one.
    - `"block"` — the submitting thread waits for space. Favors completeness
      over the non-blocking guarantee; only choose this if the caller can
      tolerate an occasional stall.

    Either drop policy logs at most one warning per minute while actively
    dropping, not one per dropped item.
    """

    def __init__(
        self,
        *,
        max_queue_size: int = 10_000,
        backpressure: BackpressurePolicy = "drop_oldest",
    ) -> None:
        """Starts the background dispatch thread immediately — a worker is
        active as soon as it's constructed, no separate "start" call
        needed. See the class docstring for what `backpressure` does once
        `max_queue_size` is reached."""
        if max_queue_size < 1:
            raise ValueError(f"max_queue_size must be >= 1, got {max_queue_size}")
        if backpressure not in _VALID_POLICIES:
            raise ValueError(f"backpressure must be one of {_VALID_POLICIES}, got {backpressure!r}")
        self.max_queue_size = max_queue_size
        self.backpressure = backpressure
        self._queue: deque[WorkItem] = deque()
        self._pending = 0
        self._closed = False
        self._cond = threading.Condition()
        self._last_drop_warning: float | None = None
        self._thread = threading.Thread(target=self._run, name="logquill-worker", daemon=True)
        self._thread.start()

    @property
    def qsize(self) -> int:
        """Number of items currently queued (not yet started). Testing/introspection only."""
        with self._cond:
            return len(self._queue)

    def submit(self, item: WorkItem) -> None:
        """Enqueue `item` for the background thread to run. Never blocks
        the caller unless `backpressure="block"` and the queue is full.
        Silently dropped if the worker has already been `close()`d.
        """
        with self._cond:
            if self._closed:
                return
            if len(self._queue) >= self.max_queue_size:
                if self.backpressure == "block":
                    while len(self._queue) >= self.max_queue_size and not self._closed:
                        self._cond.wait()
                    if self._closed:
                        return
                elif self.backpressure == "drop_newest":
                    self._warn_dropping()
                    return
                else:  # drop_oldest
                    self._queue.popleft()
                    self._pending -= 1
                    self._warn_dropping()
            self._queue.append(item)
            self._pending += 1
            self._cond.notify_all()

    def _warn_dropping(self) -> None:
        now = time.monotonic()
        # `None` rather than 0.0: the monotonic clock's zero point is arbitrary
        # (often boot time), so a fresh process could otherwise be inside the
        # warning interval already and never warn about its first drops
        last = self._last_drop_warning
        if last is None or now - last >= _DROP_WARNING_INTERVAL_SECONDS:
            self._last_drop_warning = now
            _logger.warning(
                "AsyncWorker: queue full at max_queue_size=%d, dropping records "
                "under backpressure=%r — the consumer (a transport) isn't keeping "
                "up, or max_queue_size is set too low for this burst rate",
                self.max_queue_size,
                self.backpressure,
            )

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._closed:
                    self._cond.wait()
                if not self._queue:
                    return
                item = self._queue.popleft()
                self._cond.notify_all()
            try:
                item()
            except Exception:
                _logger.exception("AsyncWorker: a queued work item raised")
            finally:
                with self._cond:
                    self._pending -= 1
                    self._cond.notify_all()

    def drain(self, timeout: float | None = None) -> bool:
        """Block the calling thread until every item submitted so far has
        finished running, or `timeout` seconds elapse (`None` waits
        indefinitely). Returns whether the queue actually fully drained.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while self._pending > 0:
                if deadline is None:
                    self._cond.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return self._pending == 0
                    self._cond.wait(remaining)
            return True

    async def drain_async(self, timeout: float | None = None) -> bool:
        """`asyncio`-friendly `drain()`: runs the blocking wait in an
        executor thread so it doesn't block the event loop while waiting.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.drain, timeout)

    def close(self, timeout: float | None = 5.0) -> bool:
        """Drain, then stop the background thread. Idempotent. Returns
        whether the drain completed within `timeout` before shutdown.
        """
        drained = self.drain(timeout)
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        self._thread.join(timeout=1.0)
        return drained
