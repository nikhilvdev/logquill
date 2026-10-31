from __future__ import annotations


from logquill import _native
from logquill.core.levels import Level, parse_level
from logquill.core.records import LogRecord
from logquill.plugins.plugin import Plugin, write_released
from logquill.transports.transport import Transport


class FlightRecorderPlugin(Plugin):
    """A bounded, per-run ring buffer of low-level records, shipped only if
    that run later errors — generalizing `SamplingPlugin`'s tail-based
    elevation from "a record sampling happened to drop" to "a record below
    the level you actually want shipped, period."

    The idea: run a logger at `DEBUG`/`TRACE` so fine-grained context is
    always *available*, but don't pay to ship that volume to a transport
    for every run — only for the runs that turn out to matter. A record at
    or above `ship_at` (default `INFO`) goes straight through, same as
    without this plugin. A record below `ship_at` is instead buffered under
    its `meta[run_key]`. If any later record for that same run reaches
    `flush_at` (default `ERROR`), every buffered record for that run is
    flushed straight to `transports` and the run ships unconditionally from
    then on — so a failing run ships its full `DEBUG` trail, and a passing
    one never pays to ship any of it.

    Flushing writes directly to `transports` (the same list given to the
    `Logger`), bypassing `before_log`/`after_log` for any plugin *after*
    this one in the pipeline, the same tradeoff `SamplingPlugin` makes —
    put `FlightRecorderPlugin` last if that matters for your pipeline.

    Buffering is bounded: at most `max_buffered_records` records total and
    `max_runs` distinct run ids are held at once. Once either limit is hit,
    the oldest buffered run is evicted (and lost, not flushed) — a
    deliberate bounded-memory trade-off: an unbounded per-run buffer would
    let one pathologically long or high-cardinality run grow memory without
    limit, which is exactly the failure mode this plugin exists to avoid
    while still keeping DEBUG-level detail available for runs that error.
    A record with no `meta[run_key]` isn't part of any run this plugin can
    buffer, so it's just passed through as-is regardless of level.
    """

    def __init__(
        self,
        *,
        transports: list[Transport],
        run_key: str = "run_id",
        ship_at: int | str | Level = Level.INFO,
        flush_at: int | str | Level = Level.ERROR,
        max_buffered_records: int = 1000,
        max_runs: int = 200,
    ) -> None:
        """`transports` must be the same list given to the `Logger` — this
        is how a flushed run's buffered records actually reach a sink.
        `max_buffered_records`/`max_runs` bound the buffer's memory; the
        oldest run is evicted (unflushed) once either is exceeded."""
        self.transports = transports
        self.run_key = run_key
        self.ship_at = parse_level(ship_at)
        self.flush_at = parse_level(flush_at)
        self.max_buffered_records = max_buffered_records
        self.max_runs = max_runs
        self._native = _native.FlightRecorder(
            run_key, self.ship_at.name, self.flush_at.name, max_buffered_records, max_runs
        )

    @property
    def buffered_records(self) -> int:
        """How many low-level records are held waiting to see whether their run errors."""
        return self._native.buffered_records()  # type: ignore[no-any-return]

    def before_log(self, record: LogRecord) -> LogRecord | None:
        """Ships `record` unconditionally if it has no run id, if its run already flushed, or if
        its own level reaches `flush_at` (releasing the run's buffered records). Otherwise ships it
        if its level reaches `ship_at`, or buffers it under its run id and drops it for now. The
        logic is in Rust."""
        kept, released = self._native.decide(record)
        write_released(self.transports, released)
        return record if kept else None
