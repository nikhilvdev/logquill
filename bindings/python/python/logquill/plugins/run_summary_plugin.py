from __future__ import annotations

from logquill import _native
from logquill.core.records import LogRecord, utc_timestamp
from logquill.plugins.plugin import Plugin, write_released
from logquill.transports.transport import Transport


class RunSummaryPlugin(Plugin):
    """Emits one summary record — total tokens, cost, tool calls, retries,
    and errors — when a run's outermost span closes, so a dashboard or the
    local viewer can read a run's totals without re-walking every one of
    its records.

    A "run" is any distinct `meta[run_key]` value (default `run_id`, what
    `RunPlugin` stamps). Its close is detected as a span record (`meta.kind
    == "span"`) for that run with no `parent_span_id` — the outermost span
    `Logger.span()` produces. Aggregation happens in `after_log`, so it sees
    every record exactly as it was actually dispatched.

    Like `SamplingPlugin`'s tail-based elevation and `FlightRecorderPlugin`,
    the summary record is written directly to `transports` (the same list
    given to the `Logger`), bypassing `before_log`/`after_log` for any
    plugin *after* this one — put `RunSummaryPlugin` last if that matters
    for your pipeline (e.g. after `TamperEvidentPlugin`, the summary record
    itself won't be hash-chained).

    Bounded: at most `max_runs` runs' aggregates are held in memory at
    once — the oldest in-progress run's aggregate is dropped (not
    summarized) if a new run's first record arrives once that limit is hit,
    the same bounded-memory trade-off `SamplingPlugin`/`FlightRecorderPlugin`
    make for their own per-run/per-trace state.
    """

    def __init__(
        self,
        *,
        transports: list[Transport],
        run_key: str = "run_id",
        logger_name: str = "app.run_summary",
        max_runs: int = 1000,
    ) -> None:
        """`transports` must be the same list given to the `Logger` — see
        the class docstring for why. `logger_name` is the `logger` field the
        emitted summary record carries."""
        self.transports = transports
        self.run_key = run_key
        self.logger_name = logger_name
        self.max_runs = max_runs
        self._native = _native.RunSummary(run_key, logger_name, max_runs)

    def after_log(self, record: LogRecord) -> None:
        """Aggregates `record` into its run's running totals (in Rust), and emits and forgets that
        run's summary once its outermost span closes."""
        summary = self._native.observe(record, utc_timestamp())
        if summary is not None:
            write_released(self.transports, [summary])
