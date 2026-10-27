"""Per-call latency on the caller's thread, measured with the public API only so the same script
runs under logquill 2.0.0 and under this version.

    python bench.py [calls] [repeats]      # prints one JSON document

`compare.py` runs it under both and prints a table.
"""

import json
import os
import sys
import tempfile
import time

from logquill import FileTransport, Logger, RedactPlugin, Transport

CALLS = int(sys.argv[1]) if len(sys.argv) > 1 else 50_000
REPEATS = int(sys.argv[2]) if len(sys.argv) > 2 else 5


class Null(Transport):
    """A custom transport that does nothing: measures the cost of getting a record to a sink."""

    def write(self, formatted, record):
        pass


def measure(make, call):
    """The best of REPEATS runs, in nanoseconds per call."""
    best = float("inf")
    for _ in range(REPEATS):
        log, close = make()
        started = time.perf_counter_ns()
        for _ in range(CALLS):
            call(log)
        best = min(best, (time.perf_counter_ns() - started) / CALLS)
        close(log)
    return round(best, 1)


def logger(transports, **kwargs):
    def make():
        log = Logger("bench", transports=transports(), **kwargs)
        return log, lambda log: log.close(30)

    return make


def sync(**kwargs):
    # 2.0.0 spelled the default `async_dispatch=False`; this version spells synchronous the same way.
    return {"async_dispatch": False, **kwargs}


devnull = os.devnull
info = lambda log: log.info("user signed up", user_id=42, plan="pro")  # noqa: E731
debug = lambda log: log.debug("filtered out", user_id=42)  # noqa: E731

results = {
    "filtered (below level)": measure(logger(lambda: [Null()], level="WARN", **sync()), debug),
    "sync, custom transport": measure(logger(lambda: [Null()], **sync()), info),
    "sync, file transport": measure(logger(lambda: [FileTransport(devnull)], **sync()), info),
    "sync, file + redaction": measure(
        logger(lambda: [FileTransport(devnull)], plugins=[RedactPlugin()], **sync()), info
    ),
    "queued, file transport": measure(
        logger(lambda: [FileTransport(devnull)], async_dispatch=True, max_queue_size=1_000_000), info
    ),
}

# Memory while a transport is stalled and the queue is bounded: peak RSS growth over a burst.
import resource  # noqa: E402
import threading  # noqa: E402

gate = threading.Event()


class Stalled(Transport):
    def write(self, formatted, record):
        gate.wait(60)


def peak_kib():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value // 1024 if sys.platform == "darwin" else value


before = peak_kib()
log = Logger("burst", transports=[Stalled()], async_dispatch=True, max_queue_size=10_000)
for i in range(300_000):
    log.info("burst", i=i, payload="x" * 100)
results["peak memory growth, 300k records into a stalled sink (KiB)"] = peak_kib() - before
gate.set()
log.close(30)

print(json.dumps({"python": sys.version.split()[0], "calls": CALLS, "results": results}))
