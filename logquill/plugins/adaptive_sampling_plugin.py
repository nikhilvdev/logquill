from __future__ import annotations

import json
import random
import time
from typing import Callable

from logquill.core.levels import Level, parse_level
from logquill.core.records import LogRecord
from logquill.plugins.plugin import Plugin


class AdaptiveSamplingPlugin(Plugin):
    """Keeps every error and every "slow" span unconditionally, samples
    everything else, and caps how many bytes of that ordinary traffic pass
    through in any one second — adjusting how much it keeps as traffic
    rises and falls, rather than a single fixed rate.

    - **Always kept**: a record at `keep_at` (default `ERROR`) or above, and
      a span record (`meta.kind == "span"`) whose `meta.duration_ms` reaches
      `slow_ms` (default 1000) — the two categories you can't afford to
      lose to sampling, independent of the budget below.
    - **Everything else** is rate-sampled at this plugin's *current* rate
      (starting at `base_rate`), then checked against a rolling
      one-second byte budget (`max_bytes_per_second`, estimated the same
      way `BatchingTransport` estimates buffered size — JSON-encoded
      length). A record that passes the rate check but would push the
      current second over budget is dropped anyway.
    - **Adaptive**: at the end of each one-second window, the rate adjusts
      based on *demand* — the byte size of every record that passed the
      rate check that window, whether or not the budget gate then also let
      it through — against the budget: well under it raises the rate (more
      gets through), over it lowers the rate (bounded to `[min_rate,
      max_rate]`). Demand, not bytes actually emitted, is what the
      adjustment has to track: once the budget is saturated, emitted bytes
      stop growing no matter how much more is being asked for, so using
      emitted bytes as the signal would read a saturated gate as "plenty of
      room" and keep raising the rate into an already-over-budget window.

    `rng`/`clock` are injectable for deterministic tests; they default to
    `random.random`/`time.monotonic`.
    """

    def __init__(
        self,
        *,
        base_rate: float = 0.1,
        min_rate: float = 0.01,
        max_rate: float = 1.0,
        keep_at: int | str | Level = Level.ERROR,
        slow_ms: float = 1000.0,
        max_bytes_per_second: int = 100_000,
        window_seconds: float = 1.0,
        rng: Callable[[], float] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """`base_rate` is the starting (and, absent any traffic, steady-
        state) sample rate for ordinary records; it adapts within
        `[min_rate, max_rate]` from there. Raises `ValueError` if any rate
        is outside `[0.0, 1.0]`, or `min_rate > max_rate`."""
        for name, value in (
            ("base_rate", base_rate),
            ("min_rate", min_rate),
            ("max_rate", max_rate),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1, got {value!r}")
        if min_rate > max_rate:
            raise ValueError(f"min_rate ({min_rate!r}) must be <= max_rate ({max_rate!r})")

        self.rate = base_rate
        self.min_rate = min_rate
        self.max_rate = max_rate
        self.keep_at = parse_level(keep_at)
        self.slow_ms = slow_ms
        self.max_bytes_per_second = max_bytes_per_second
        self.window_seconds = window_seconds
        self._rng = rng or random.random
        self._clock = clock or time.monotonic

        self._window_start = self._clock()
        self._window_demand_bytes = 0
        self._window_emitted_bytes = 0

    def _is_always_kept(self, record: LogRecord) -> bool:
        if Level[record["level"]] >= self.keep_at:
            return True
        meta = record["meta"]
        if meta.get("kind") != "span":
            return False
        duration = meta.get("duration_ms")
        return (
            isinstance(duration, (int, float))
            and not isinstance(duration, bool)
            and duration >= self.slow_ms
        )

    def _estimate_size(self, record: LogRecord) -> int:
        try:
            return len(json.dumps(record, separators=(",", ":"), default=str).encode("utf-8"))
        except (TypeError, ValueError, RecursionError):
            return len(str(record))

    def _roll_window_if_due(self) -> None:
        now = self._clock()
        elapsed = now - self._window_start
        if elapsed < self.window_seconds:
            return

        demand = (
            self._window_demand_bytes / self.max_bytes_per_second
            if self.max_bytes_per_second
            else 0.0
        )
        if demand < 0.5:
            self.rate = min(self.rate * 1.2, self.max_rate)
        elif demand > 0.9:
            self.rate = max(self.rate * 0.5, self.min_rate)

        # Each window starts fresh from `now`, not `now - elapsed`, so a long
        # gap between records (e.g. idle traffic) doesn't retroactively
        # "owe" several missed windows' worth of rate adjustments at once.
        self._window_start = now
        self._window_demand_bytes = 0
        self._window_emitted_bytes = 0

    def before_log(self, record: LogRecord) -> LogRecord | None:
        """Keeps an error/slow-span record unconditionally; otherwise
        samples at the current rate and checks the byte budget, dropping
        the record if either fails."""
        self._roll_window_if_due()

        if self._is_always_kept(record):
            return record

        if self._rng() >= self.rate:
            return None

        size = self._estimate_size(record)
        self._window_demand_bytes += size

        if self._window_emitted_bytes + size > self.max_bytes_per_second:
            return None

        self._window_emitted_bytes += size
        return record
