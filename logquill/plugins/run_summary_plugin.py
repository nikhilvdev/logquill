from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from logquill.core.levels import Level
from logquill.core.records import LogRecord, create_record
from logquill.plugins.plugin import Plugin
from logquill.transports.transport import Transport


@dataclass
class _RunStats:
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    tool_calls: int = 0
    retries: int = 0
    errors: int = 0
    record_count: int = 0
    root_span_id: str | None = None
    duration_ms: float | None = None
    _extra: dict[str, Any] = field(default_factory=dict)


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
        self._stats: OrderedDict[object, _RunStats] = OrderedDict()

    def after_log(self, record: LogRecord) -> None:
        """Aggregates `record` into its run's running totals, and emits (and
        forgets) that run's summary once its outermost span closes."""
        meta = record["meta"]
        run_id = meta.get(self.run_key)
        if run_id is None:
            return

        stats = self._stats.get(run_id)
        if stats is None:
            if len(self._stats) >= self.max_runs:
                self._stats.popitem(last=False)
            stats = _RunStats()
            self._stats[run_id] = stats
        else:
            self._stats.move_to_end(run_id)

        stats.record_count += 1
        if Level[record["level"]] >= Level.ERROR:
            stats.errors += 1
        retry_count = meta.get("retry_count")
        if isinstance(retry_count, int) and not isinstance(retry_count, bool):
            stats.retries += retry_count
        if meta.get("kind") == "action" and isinstance(meta.get("tool"), str):
            stats.tool_calls += 1

        llm = record.get("llm")
        if isinstance(llm, dict):
            if isinstance(llm.get("tokens_in"), int) and not isinstance(llm.get("tokens_in"), bool):
                stats.tokens_in += llm["tokens_in"]
            if isinstance(llm.get("tokens_out"), int) and not isinstance(
                llm.get("tokens_out"), bool
            ):
                stats.tokens_out += llm["tokens_out"]
            cost = llm.get("cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                stats.cost_usd += cost

        is_root_span = (
            meta.get("kind") == "span"
            and isinstance(meta.get("span_id"), str)
            and "parent_span_id" not in meta
        )
        if is_root_span:
            stats.root_span_id = meta.get("span_id")
            duration = meta.get("duration_ms")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                stats.duration_ms = float(duration)
            del self._stats[run_id]
            self._emit_summary(run_id, stats)

    def _emit_summary(self, run_id: object, stats: _RunStats) -> None:
        summary_meta: dict[str, Any] = {
            "kind": "run_summary",
            self.run_key: run_id,
            "tokens_in": stats.tokens_in,
            "tokens_out": stats.tokens_out,
            "cost_usd": stats.cost_usd,
            "tool_calls": stats.tool_calls,
            "retries": stats.retries,
            "errors": stats.errors,
            "record_count": stats.record_count,
        }
        if stats.duration_ms is not None:
            summary_meta["duration_ms"] = stats.duration_ms
        if stats.root_span_id is not None:
            summary_meta["parent_span_id"] = stats.root_span_id

        summary = create_record(
            level=Level.INFO, logger=self.logger_name, message="run summary", meta=summary_meta
        )
        for transport in self.transports:
            transport.write(transport.format(summary), summary)
