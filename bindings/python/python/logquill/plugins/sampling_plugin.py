from __future__ import annotations

import random
from typing import Callable

from logquill import _native

from logquill.core.levels import Level, parse_level
from logquill.core.records import LogRecord
from logquill.plugins.plugin import Plugin, write_released
from logquill.transports.transport import Transport


class SamplingPlugin(Plugin):
    """Keeps roughly `rate` of records (0.0-1.0), dropping the rest.

    With `transports` set, sampling becomes tail-based per trace: a record
    that would otherwise be dropped is buffered under its `meta[trace_key]`
    value instead of discarded outright. If any later record sharing that
    trace id reaches `elevate_at` or above, the whole trace is "elevated" —
    every buffered record for that trace id is flushed straight to
    `transports`, and every subsequent record for that trace id ships
    unconditionally. This is what lets a sampled-out request still produce
    a complete trace once it turns out to matter (it errored).

    Flushing writes buffered records directly to `transports` — pass the
    same list given to the `Logger`. This bypasses `before_log`/`after_log`/
    `on_error` for any plugin *after* `SamplingPlugin` in the pipeline (the
    plugins before it already ran, since that's how the buffered record was
    built); put `SamplingPlugin` last if that matters for your pipeline.

    Without `transports`, tail-based elevation is inactive and this behaves
    exactly like plain rate-based sampling (the original behavior) — a
    record without `meta[trace_key]` is also just rate-sampled, since there's
    no trace to buffer it under.

    Buffering is bounded: at most `max_buffered_records` records total and
    `max_traces` distinct trace ids are held at once. Once either limit is
    hit, the oldest buffered trace is evicted (and its records are lost, not
    flushed) — a deliberate bounded-memory trade-off, not a bug: an
    unbounded per-trace buffer would let a single pathologically long-lived
    or high-cardinality trace grow memory without limit.
    """

    def __init__(
        self,
        rate: float,
        *,
        rng: Callable[[], float] | None = None,
        trace_key: str = "trace_id",
        elevate_at: int | str | Level = Level.ERROR,
        transports: list[Transport] | None = None,
        max_buffered_records: int = 1000,
        max_traces: int = 200,
    ) -> None:
        """`rng` is injectable for deterministic testing of the sample
        decision; defaults to `random.random`. `transports` opts into
        tail-based elevation — see the class docstring — and must be the
        same transport list given to the `Logger` for buffered records to
        actually reach the intended sinks. `max_buffered_records`/
        `max_traces` bound the tail-buffer's memory; the oldest trace is
        evicted (unflushed) once either is exceeded."""
        self.rate = rate
        self._rng = rng or random.random
        self.trace_key = trace_key
        self.elevate_at = parse_level(elevate_at)
        self.transports = transports
        self.max_buffered_records = max_buffered_records
        self.max_traces = max_traces
        self._native = _native.Sampling(
            rate,
            transports is not None,
            trace_key,
            self.elevate_at.name,
            max_buffered_records,
            max_traces,
        )

    @property
    def buffered_records(self) -> int:
        """How many dropped records are held waiting to see whether their trace errors."""
        return self._native.buffered_records()  # type: ignore[no-any-return]

    def before_log(self, record: LogRecord) -> LogRecord | None:
        """Keeps `record` per the sample rate, buffers it under its trace id if dropped and
        tail-based elevation is active, or — if this or an earlier record for the same trace
        reached `elevate_at` — releases the whole buffered trace and lets `record` through
        unconditionally. The logic is in Rust."""
        kept, released = self._native.decide(record, self._rng())
        write_released(self.transports, released)
        return record if kept else None
