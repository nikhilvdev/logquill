# Migrating from logquill 2.x to 3.0 (Python)

3.0 runs the queue, worker, formatters, redaction, hash chain and the native transports in Rust, and keeps the Python API of
2.0.0. Code that calls `Logger`, `logger.info(...)` and the plugins through `from logquill import ...` needs no change. What
changes is a short list of deliberate behaviours, how the package is installed, and where a few things happen. Each behaviour
is a settled decision in `docs/spec/00-decisions.md`; the number is given so you can read the reasoning.

> **Not released yet.** 3.0 is built from the `develop` branch and nothing is on PyPI. Until a release, install from a
> checkout (`maturin develop --release` in `bindings/python`). The licence of the 3.0 line is undecided
> (`docs/rust-core-plan.md`, section 8); 2.0.0 stays FSL-1.1-MIT and 1.x and earlier stay MIT.

## 1. Install

| | 2.x | 3.0 |
|---|---|---|
| Wheel | pure Python | a compiled wheel, one `abi3` wheel per platform (Python 3.10 and newer) |
| Python | 3.10 – 3.14 | 3.10 – 3.14, including free-threaded 3.13t / 3.14t |
| Build from source | `pip install .` | needs a Rust toolchain (`maturin`) |

On a platform without a wheel, pip builds the package from the source distribution, which needs Rust. The pure-Python 2.x
line stays on PyPI and on the `main` branch.

## 2. Behaviour that changed

- **Dispatch is queued by default (D2).** `async_dispatch=True` is the default. A log call hands the record to a worker
  thread and returns. `flush()` and `close()` wait for the queue, and an unclosed logger still flushes at exit. To write on
  the caller's thread, as 2.x did by default, pass `async_dispatch=False`.
- **The chain is made when a record is written (D22).** `TamperEvidentPlugin` numbers records in write order, so the chain is
  valid under many threads. The record a log call *returns* therefore has no `meta.hash` yet; the line that is written does.
  Read hashes from the file, not from the return value.
- **A redaction plugin that fails drops the record (D20).** In 2.x a redactor that raised let the record through unredacted.
  A plugin class sets `fails_closed = True` to get the same treatment.
- **`RedactPlugin` matches keys at every depth (D27), and PII scanning covers every non-JSON leaf (D21).** Output that used to
  keep a nested `password` now masks it. Compare your redaction tests against real output.
- **JSON lines are UTF-8, not `\uXXXX`-escaped (D32).** Split a log file on `"\n"`, not `str.splitlines()`: `splitlines`
  also breaks on a few Unicode separators that can now appear inside a line.
- **Console colour is on only for a terminal and when `NO_COLOR` is unset (D9).**
- **The "content capture is off" placeholder is one string in every language (D3).** A test that compares the placeholder
  text must use the 3.0 string.
- **Dropping a `Logger` writes its queued records and flushes, but no longer closes transports it shares.** Close shared
  transports yourself.
- **`parse_record` error text** names JSON types (`array`, `number`) and quotes with `"`, because the core is shared.

## 3. What did not change

The record shape, `schema_version`, the `llm` block, the level weights, the config format, the CLI commands (`tail`, `trace`,
`serve`, `dev`, `verify`) and the canonical JSON the hash chain is computed over are the 2.0 contract. A log file written by
2.0.0 verifies under 3.0, and the reverse. The 3.0 test suite includes oracle tests that run the same scenarios against the
published 2.0.0 and compare the output, with the differences above masked, so nothing else is allowed to change quietly.

## 4. New in 3.0

- `Logger("api", meta={...})`: fixed `meta` on every record, inherited by children.
- `install_signal_handlers(log)`: on SIGTERM or SIGINT, drain the queue, then exit with 128 plus the signal.
- `logquill.bridges.loguru_sink` and `structlog_processor`, and `logquill.tracing.otel_span_processor.OtelSpanProcessor`.
- A fork flushes every live logger first, so a child process never inherits a held transport lock.
- Speed: caller-side latency is lower than 2.0.0 on every row the benchmark gate measures except the queued hand-off, which
  swings on shared CI runners (`bindings/python/bench/compare.py`).

## 5. Checklist

1. Install the 3.0 wheel into a fresh environment and run your test suite.
2. Search your tests for `splitlines`, redaction of nested keys, and the content-off placeholder; those are the places a
   behaviour change shows up as a test failure.
3. If you read `meta.hash` from a returned record, read it from the written line instead.
4. If you relied on a log call being synchronous, pass `async_dispatch=False` or call `flush()` where you need the line on
   disk.
5. Verify an existing log with `logquill verify FILE`; it should pass unchanged.
