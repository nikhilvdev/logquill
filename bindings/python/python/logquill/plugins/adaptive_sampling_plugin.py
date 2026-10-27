from __future__ import annotations

import random
import time
from typing import Callable

from logquill import _native
from logquill.core.levels import Level, parse_level
from logquill.core.records import LogRecord
from logquill.plugins.plugin import Plugin


class AdaptiveSamplingPlugin(Plugin):
    """Keeps every error and slow span, samples everything else, and caps how many bytes of that
    ordinary traffic pass through in any one second — adjusting how much it keeps as traffic rises
    and falls, rather than a single fixed rate. The logic is in Rust.

    - **Always kept**: a record at `keep_at` (default `ERROR`) or above, and a span record
      (`meta.kind == "span"`) whose `meta.duration_ms` reaches `slow_ms` (default 1000) — the two
      categories you can't afford to lose to sampling, independent of the budget below.
    - **Everything else** is rate-sampled at this plugin's *current* rate (starting at
      `base_rate`), then checked against a rolling byte budget (`max_bytes_per_second`, estimated
      from the record's JSON-encoded length). A record that passes the rate check but would push
      the current window over budget is dropped anyway.
    - **Adaptive**: at the end of each window, the rate adjusts based on *demand* — the byte size of
      every record that passed the rate check that window, whether or not the budget gate then also
      let it through — against the budget: well under it raises the rate (more gets through), over it
      lowers the rate (bounded to `[min_rate, max_rate]`). Demand, not bytes actually emitted, is
      what the adjustment has to track: once the budget is saturated, emitted bytes stop growing no
      matter how much more is being asked for.

    `rng`/`clock` are injectable for deterministic tests; they default to `random.random` and
    `time.monotonic`.
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
        """`base_rate` is the starting (and, in a steady state, the resting) sample rate for
        ordinary records; it adapts within `[min_rate, max_rate]` from there. Raises `ValueError` if
        any rate is outside `[0.0, 1.0]`, or `min_rate > max_rate`."""
        self.min_rate = min_rate
        self.max_rate = max_rate
        self.keep_at = parse_level(keep_at)
        self.slow_ms = slow_ms
        self.max_bytes_per_second = max_bytes_per_second
        self.window_seconds = window_seconds
        self._rng = rng or random.random
        self._clock = clock or time.monotonic
        self._native = _native.AdaptiveSampling(
            self._clock(),
            base_rate,
            min_rate,
            max_rate,
            self.keep_at.name,
            slow_ms,
            max_bytes_per_second,
            window_seconds,
        )

    @property
    def rate(self) -> float:
        """The sample rate in force now; it moves within `[min_rate, max_rate]` as demand does."""
        return self._native.rate  # type: ignore[no-any-return]

    def before_log(self, record: LogRecord) -> LogRecord | None:
        """Rolls the window if it is due, always keeps an error or a slow span, and otherwise
        samples at the current rate and checks the byte budget, dropping the record if either
        fails."""
        return record if self._native.decide(record, self._clock(), self._rng()) else None
