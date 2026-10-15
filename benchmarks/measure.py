"""Memory measurements for the logging hot path, and the budgets CI holds them to.

These measure bytes and object counts, not wall-clock time: allocation
numbers are close to deterministic for a given interpreter, so a budget can
fail the build on a real regression without flaking on a busy CI runner the
way a timing threshold would.

Run them with `pytest benchmarks`. They're kept out of the default `pytest`
run (and out of the coverage job) because line tracing distorts allocation
counts.
"""

from __future__ import annotations

import gc
import statistics
import sys
import threading
import tracemalloc
from typing import Callable

from logquill import ContextPlugin, Logger, PIIRedactPlugin, Plugin, RedactPlugin
from logquill.core.records import LogRecord
from logquill.transports.transport import Transport

#: The most a metric may be before the build fails. Each is a few times what
#: the current code measures, so ordinary interpreter-to-interpreter variation
#: passes and a change that makes the hot path materially heavier doesn't.
BUDGETS: dict[str, float] = {
    # net objects still alive after a log call, per call — a fixed few hundred
    # one-off allocations amortize to ~0.01 over the run; anything near 1 means
    # the hot path is accumulating state
    "retained_blocks_per_call": 0.05,
    # transient bytes at the peak of one call: level-filtered (never builds a record)
    "peak_bytes_filtered_call": 1_000,
    # ... a call through no plugins, and through a realistic plugin stack
    "peak_bytes_plain_call": 12_000,
    "peak_bytes_plugin_call": 14_000,
    # memory growth over a 100k-record burst into a stalled transport, with a
    # bounded queue — the queue bound, not the burst size, must set this
    "peak_bytes_stalled_burst": 3_000_000,
}

_META = {"user_id": 42, "route": "/checkout", "duration_ms": 12.5, "ok": True, "tag": "a" * 40}


class NullTransport(Transport):
    """Discards everything: isolates the logger's own cost from any sink's."""

    def write(self, formatted: str, record: LogRecord) -> None:
        pass


class StalledTransport(Transport):
    """Blocks in `write()` until released — a sink that's down."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()
        self.started = threading.Event()

    def write(self, formatted: str, record: LogRecord) -> None:
        self.started.set()
        self.gate.wait(timeout=60)


def plain_logger(level: str = "INFO") -> Logger:
    return Logger("bench", level=level, transports=[NullTransport()], flush_at_exit=False)


def plugin_logger() -> Logger:
    return Logger(
        "bench",
        transports=[NullTransport()],
        plugins=[ContextPlugin(service="api"), RedactPlugin(), PIIRedactPlugin()],
        flush_at_exit=False,
    )


def retained_blocks_per_call(logger: Logger, calls: int = 20_000) -> float:
    """Net change in live allocated objects per call, with the cyclic GC held
    off so it can't mask a leak or add noise."""
    for _ in range(200):
        logger.info("warmup", **_META)
    gc.collect()
    gc.disable()
    try:
        before = sys.getallocatedblocks()
        for _ in range(calls):
            logger.info("bench", **_META)
        after = sys.getallocatedblocks()
    finally:
        gc.enable()
    return (after - before) / calls


def peak_bytes_per_call(call: Callable[[], object], samples: int = 100) -> float:
    """Median, over `samples` calls, of how far traced memory climbed above
    its starting level during one call — the transient cost of a log call."""
    for _ in range(50):
        call()
    gc.collect()
    peaks: list[int] = []
    for _ in range(samples):
        tracemalloc.start()
        try:
            baseline, _peak = tracemalloc.get_traced_memory()
            call()
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        peaks.append(peak - baseline)
    return statistics.median(peaks)


def peak_bytes_stalled_burst(
    *, burst: int = 100_000, max_queue_size: int = 1_000, policy: str = "drop_oldest"
) -> float:
    """Memory growth while `burst` records go into a queue whose consumer is
    stalled for the whole burst."""
    transport = StalledTransport()
    logger = Logger(
        "bench",
        transports=[transport],
        async_dispatch=True,
        max_queue_size=max_queue_size,
        backpressure=policy,  # type: ignore[arg-type]
        flush_at_exit=False,
    )
    logger.info("stall the worker", **_META)
    transport.started.wait(timeout=10)
    gc.collect()
    tracemalloc.start()
    try:
        baseline, _peak = tracemalloc.get_traced_memory()
        for _ in range(burst):
            logger.info("burst", **_META)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        transport.gate.set()
        logger.close(timeout=30)
    return float(peak - baseline)


def measure_all() -> dict[str, float]:
    """Every metric in `BUDGETS`, for the logger configurations the library ships."""
    filtered = plain_logger(level="ERROR")
    plain = plain_logger()
    with_plugins = plugin_logger()
    return {
        "retained_blocks_per_call": retained_blocks_per_call(plain),
        "peak_bytes_filtered_call": peak_bytes_per_call(lambda: filtered.info("x", **_META)),
        "peak_bytes_plain_call": peak_bytes_per_call(lambda: plain.info("x", **_META)),
        "peak_bytes_plugin_call": peak_bytes_per_call(lambda: with_plugins.info("x", **_META)),
        "peak_bytes_stalled_burst": peak_bytes_stalled_burst(),
    }


def violations(metrics: dict[str, float], budgets: dict[str, float] = BUDGETS) -> list[str]:
    """One human-readable line per metric that's over its budget (or missing)."""
    problems = []
    for name, limit in budgets.items():
        value = metrics.get(name)
        if value is None:
            problems.append(f"{name}: not measured")
        elif value > limit:
            problems.append(f"{name}: {value:,.1f} is over the budget of {limit:,.1f}")
    return problems


class Leaky(Plugin):
    """A deliberately regressed plugin, used to prove the gate can fail: it
    keeps every record it sees, so memory grows with every call."""

    def __init__(self) -> None:
        self.seen: list[LogRecord] = []

    def before_log(self, record: LogRecord) -> LogRecord | None:
        self.seen.append(record)
        return record


class Bloated(Plugin):
    """Another deliberate regression: allocates a large transient buffer per call."""

    def before_log(self, record: LogRecord) -> LogRecord | None:
        record["meta"]["scratch"] = ["x" * 100 for _ in range(2_000)]
        return record


if __name__ == "__main__":  # pragma: no cover
    results = measure_all()
    for name, value in results.items():
        print(f"{name:32} {value:>14,.1f}  (budget {BUDGETS[name]:g})")
    problems = violations(results)
    sys.exit("\n".join(problems) if problems else 0)
