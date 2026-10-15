from __future__ import annotations

from collections import OrderedDict

from logquill.core.levels import Level, parse_level
from logquill.core.records import LogRecord
from logquill.plugins.plugin import Plugin
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
        self._buffer: OrderedDict[object, list[LogRecord]] = OrderedDict()
        self._buffered_count = 0
        self._flushed: OrderedDict[object, None] = OrderedDict()

    def before_log(self, record: LogRecord) -> LogRecord | None:
        """Ships `record` unconditionally if it has no run id, if its run
        already flushed, or if its own level reaches `flush_at` (triggering
        the flush). Otherwise ships it if its level reaches `ship_at`, or
        buffers it under its run id and drops it for now."""
        run_id = record["meta"].get(self.run_key)
        if run_id is None:
            return record

        if run_id in self._flushed:
            return record

        level = Level[record["level"]]
        if level >= self.flush_at:
            self._flush(run_id)
            return record

        if level >= self.ship_at:
            return record

        self._buffer_record(run_id, record)
        return None

    def _flush(self, run_id: object) -> None:
        self._flushed[run_id] = None
        buffered = self._buffer.pop(run_id, [])
        self._buffered_count -= len(buffered)
        for buffered_record in buffered:
            for transport in self.transports:
                transport.write(transport.format(buffered_record), buffered_record)

    def _buffer_record(self, run_id: object, record: LogRecord) -> None:
        if run_id in self._buffer:
            self._buffer.move_to_end(run_id)
        else:
            if len(self._buffer) >= self.max_runs:
                self._evict_oldest_run()
            self._buffer[run_id] = []

        self._buffer[run_id].append(record)
        self._buffered_count += 1

        while self._buffered_count > self.max_buffered_records and self._buffer:
            self._evict_oldest_run()

    def _evict_oldest_run(self) -> None:
        _, oldest_records = self._buffer.popitem(last=False)
        self._buffered_count -= len(oldest_records)
