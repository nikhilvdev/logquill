from __future__ import annotations

import time
from collections import OrderedDict
from typing import Callable, Hashable

from logquill import _native
from logquill.core.records import LogRecord
from logquill.plugins.plugin import Plugin

RateLimitKeyFunc = Callable[[LogRecord], Hashable]


def _default_key(record: LogRecord) -> Hashable:
    return (record["logger"], record["level"])


class RateLimitPlugin(Plugin):
    """Drops records once a key — by default `(logger, level)` — exceeds
    `max_records` within a rolling `per_seconds` window, to cap a noisy loop
    (a retry that logs the same error every iteration, a hot path that logs
    once per request) without silencing the logger's other messages.

    Each key gets its own fixed window: the count for a key resets
    `per_seconds` after that *key's own* first record in the current window,
    not on a shared global clock, so unrelated keys never reset in lockstep.

    Pass `key_func` to rate-limit on something other than `(logger, level)`
    — e.g. per error message, or per a `meta` field identifying the caller.

    Bounded by `max_keys` distinct keys tracked at once; past that, the
    least-recently-seen key's window is evicted to make room for a new one
    — the same bounded-memory trade-off `SamplingPlugin` makes for trace
    buffering, since an unbounded key space (e.g. rate-limiting per user id)
    would otherwise grow memory without limit.
    """

    def __init__(
        self,
        max_records: int,
        per_seconds: float,
        *,
        key_func: RateLimitKeyFunc = _default_key,
        max_keys: int = 1000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`clock` is injectable for deterministic testing of window
        rollover; defaults to `time.monotonic` so wall-clock adjustments
        can't shrink or extend a window."""
        if max_records < 1:
            raise ValueError(f"max_records must be at least 1, got {max_records!r}")
        if per_seconds <= 0:
            raise ValueError(f"per_seconds must be positive, got {per_seconds!r}")
        self.max_records = max_records
        self.per_seconds = per_seconds
        self.key_func = key_func
        self.max_keys = max_keys
        self._clock = clock
        # The default key (logger, level) is counted in Rust. A custom `key_func` can return any
        # Python hashable, which Rust cannot hold, so that case keeps the Python counting below.
        self._native = (
            _native.RateLimit(max_records, per_seconds, max_keys) if key_func is _default_key else None
        )
        # value: (window_start, count_in_window)
        self._windows: OrderedDict[Hashable, tuple[float, int]] = OrderedDict()

    def before_log(self, record: LogRecord) -> LogRecord | None:
        """Drops `record` if its key's current window has already reached
        `max_records`; starts a fresh window for a key whose previous
        window has expired."""
        if self._native is not None:
            return record if self._native.allow(record, self._clock()) else None
        key = self.key_func(record)
        now = self._clock()
        window = self._windows.get(key)

        if window is None or now - window[0] >= self.per_seconds:
            self._windows[key] = (now, 1)
            self._windows.move_to_end(key)
            while len(self._windows) > self.max_keys:
                self._windows.popitem(last=False)
            return record

        window_start, count = window
        self._windows.move_to_end(key)
        if count >= self.max_records:
            return None

        self._windows[key] = (window_start, count + 1)
        return record
