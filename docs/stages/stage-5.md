# Stage 5 — Python binding and SDK

Goal: `pip install logquill` gives the Python API of 2.0.0 on the Rust core. Plan: `docs/rust-core-plan.md`
(sections 3 and 5, Stage 5). Code: `bindings/python/` (PyO3 + maturin, mixed layout: the Rust extension
`logquill._native` plus the pure-Python SDK in `python/logquill/`).

## Status: complete

All seven slices are implemented and every exit criterion passes locally (ported suite and oracle, adversarial
`meta`, benchmark against 2.0.0, free-threaded 3.14t, wheel smoke test). The new CI jobs in `ci.yml` have run on
GitHub and are green: on the PR (run 40495441108) and on `develop` after the merge (run 40496227591), and on every
`develop` run since (latest 40586980453, 2026-10-27): the oracle `pytest` against published 2.0.0, the latency gate
(`bench/compare.py`), the 3.14t job, and the wheel build and install smoke test, on Ubuntu and macOS.

The old Python code is a specification, not a source: `docs/spec/` holds the behaviour and `docs/legacy/python/`
the old README, changelog and migration notes. The old test suite
(`docs/spec/legacy-tests/python-tests.txt`) is the checklist for the exit criterion.

## Layout decisions

- The binding is **outside the Cargo workspace** (`exclude` in the root `Cargo.toml`) with its own lockfile. PyO3's
  macros expand to `unsafe`, which the workspace forbids, and building needs Python, which `cargo test --workspace`
  on CI should not. It has its own CI job (`python binding`).
- One `abi3` wheel per platform serves Python 3.10 and later. Free-threaded builds (3.13t / 3.14t) need their own
  wheels and are not set up yet.
- Python values become JSON values in `src/convert.rs`, which treats `meta` as hostile: a structure that contains
  itself becomes `"<circular reference>"` (cycles are found by walking the current path, so a wide cycle costs
  nothing), nesting past 100 levels becomes `"<max depth exceeded>"`, other types are written with `str()`, NaN and
  infinity become `null` in logs (D42) but raise `ValueError` when hashing, and numbers keep Python's own spelling
  because the tamper chain hashes that text.

## Approach (changed from the first plan)

The published 2.0.0 wheel is the starting point, not a blank page. The pure-Python SDK (adapters,
patching, driver-based transports, config, MCP, `Logger` facade with `opt`/`lazy`/`depth`, context,
spans, `exc_info`/`diagnose`) was brought in as a working baseline, and the **core pieces are swapped
for Rust-backed ones one at a time**, each checked against the real 2.0.0 package as an oracle:
`tests/oracle/scenarios.py` runs the same scenarios under both interpreters (masking timestamps,
generated ids and timings) and `tests/test_oracle.py` compares the results. Set
`LOGQUILL_ORACLE_PYTHON` to an interpreter with `logquill==2.0.0` installed to run them; CI does.

## Slices

| # | Slice | State |
|---|---|---|
| 1 | Extension scaffold, converter, pure helpers (`parse_record`, `verify_chain`, `canonical_json`, formatters, `parse_logfmt`); golden records and chain | **done** |
| 2 | Baseline SDK in place; Rust dispatcher (bounded queue, worker, fork-safe) behind `Logger`; native console / file (rotation, gz/zip, Fernet) / HTTP transports; custom Python transports bridged; formatters, timestamp and `parse_record` in Rust | **done** |
| 3 | Plugins on the core: redaction, PII (12 patterns), tamper chain (at write time, D22), sampling with tail elevation, rate limit, adaptive sampling, flight recorder, run summary; fail-closed redactors (D20); content policy and field registry (D3) | **done** |
| 4 | Native syslog / SQLite / cloud HTTP sinks / OTLP; alerting service and senders; metrics backends; level watchers; semconv, ECS, SQL and graph rows from the core | **done** (all exposed from `_native` and used) |
| 5 | Python-only layer: config with `class:` resolution, stdlib `logging` bridge (D24), serverless and exit hooks, `toggle` (D-rules), `AsyncWorker` on the native worker; flush before `fork` (`os.register_at_fork`) | **done** |
| 6 | Gains from JS (spec 09 section B): `Logger(meta=...)`, `OtelSpanProcessor`, per-client SDK patching, `install_signal_handlers`, `structlog` and `loguru` bridges, LangChain fallback span name; adapters, instrumentation and MCP run against the new `Logger` | **done** (`tests/test_gains_from_js.py`) |
| 7 | Tests for every area, adversarial `meta` tests, benchmark against 2.0.0, free-threaded CI, wheel build and install smoke test, README and migration guide | **done**: oracle (24 scenarios) agrees with 2.0.0; adversarial `meta` test, wheel smoke test (3.10) and 3.14t run pass locally. CI jobs for the oracle, benchmark, 3.14t and wheel smoke are written (`ci.yml`, not yet run on GitHub); README has an "Upgrading to 3.0" section. Benchmark against 2.0.0: every caller-side row is at or below 2.0.0 (custom transport 0.78x, after Python-only transports on the synchronous path were handed the caller's dict directly when `is_json_safe`, falling back to the sanitising core path otherwise). **CI:** green on GitHub (see Status). One flake on the way: on Stage 6's PR the `queued, file transport` row failed the 1.10 margin twice (1.48x, then 1.12x) with no Python change on that branch, because that row times a hand-off to a background thread and swings on a shared runner. The gate now compares the median of three alternating runs per interpreter (`bench/compare.py`); the row is 0.98x and green since |

## Differences from 2.0.0 that are deliberate

Each is a settled decision in `docs/spec/00-decisions.md`; the oracle masks them so nothing else can hide.

- **D2**: dispatch is queued by default (`async_dispatch=True`); pass `async_dispatch=False` to write on the
  caller's thread. `flush()` and `close()` wait; leaving a logger unclosed still flushes at exit.
- **D3**: the "content capture is off" placeholder is one string in every language.
- **D9**: console colour is on only for a terminal and when `NO_COLOR` is unset.
- **D20**: a redaction plugin that fails drops the record instead of passing it on unredacted
  (`Plugin.fails_closed`).
- **D21**: PII scanning covers the text of every non-JSON leaf.
- **D22**: the tamper chain is made at write time, in write order, so it stays valid under many threads.
  The record a log call returns therefore has no `meta.hash` yet; the written one does.
- **D27**: `RedactPlugin` matches keys at every depth, not just the top level.
- **D32**: JSON lines are UTF-8, not `\uXXXX`-escaped.
- `parse_record` error text names JSON types (`array`, `number`) and quotes with `"`, because the core is shared.
- Dropping a `Logger` writes its queued records and flushes, but no longer closes the transports it shares.

## Exit criteria (from the plan)

- The ported Python test suite passes.
- The adversarial `meta` tests pass.
- Per-call latency and allocations are at least as good as 2.0.0.
- Free-threaded CI is green.
- An install-and-run smoke test passes from a built wheel.

## Slice 1 results

- 60 tests in `bindings/python/tests/test_helpers.py` pass on Python 3.10, 3.12 and 3.13 from one build: every
  golden valid, legacy and rejected record, the shared hash chain (so number and Unicode spelling survive the
  crossing), tamper cases, and the hostile-input cases above.
- `cargo fmt`, `cargo clippy -D warnings` clean for the binding; the main workspace is unchanged.

## Open points for later slices

- **Licence.** The binding's manifests carry no licence field: maturin refuses a file outside the directory, and the
  licence is still an open decision (plan, section 8). Copy `LICENSE` in when it is settled.
- **Version spelling.** `logquill.__version__` is `3.0.0-dev.0` (Cargo); the wheel metadata is `3.0.0.dev0` (PEP 440).
  Unify before the first release.
- `verify_chain` collects its records before checking, so memory is the log's size; the CLI's `verify` streams.
