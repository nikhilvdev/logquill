# Stage 2 — core: processing and pipeline

Goal: everything in the shared corpus that is not I/O runs in `logquill-core`, and a log call
passes through one pipeline whose failure modes are fail-closed.

## Status: complete

| Area | Where | Checked by |
|---|---|---|
| Trace headers, span / trace ids | `trace.rs` | corpus `trace-headers`, `otel-ids` |
| Key redaction and the twelve PII patterns | `redact.rs` | corpus `redaction` |
| JSON, text and logfmt lines; logfmt reader | `format.rs`, `jsonx.rs` | corpus `formatters` |
| GenAI attributes, convention selection, exact timestamps, ECS | `semconv.rs` | corpus `semconv`, `ecs` |
| SQL row and table definition, graph row and statement | `rows.rs` | corpus `sql-rows`, `graph-rows` |
| Trace-tree builder and renderer | `tree.rs` | corpus `trace-tree` |
| Sampling (tail), rate limit, adaptive sampling, flight recorder, run summary, run stamp, alert dedupe | `plugins.rs`, `ordered.rs` | corpus of the same names |
| The log-call pipeline: level, toggle, retry, content policy, plugins; shared chain | `pipeline.rs` | unit tests, `tests/concurrency.rs` |

All sixteen processing groups pass in `tests/processing.rs`. The groups were produced by running
the Python 2.0.0 and JavaScript references and applying decisions D35–D45, so passing them means
matching Python except where a decision deliberately changed it.

Exit criteria:

- **Every corpus passes in Rust:** all 22 groups (Stage 1's six plus these sixteen).
- **A raising redactor drops the record:** a plugin that returns an error *or panics* and is
  marked fail-closed (the redactors are) withholds the record; other failing plugins are skipped
  and reported as warnings. Tested in `pipeline.rs`.
- **Chain verifiable under 8 concurrent writers:** `SharedChain::write` chains and hands the record
  to the sink inside one critical section; 8 threads × 250 records verify in order
  (`tests/concurrency.rs`).

Design notes:

- The core owns no clock, random source or threads. Plugins are state machines taking the clock
  reading and random draw as arguments, so scenarios are deterministic.
- Bounded everywhere: trace buffers, rate-limit windows, flight-recorder runs, run-summary runs,
  elevated and flushed sets (10,000, least recently used first), retry tracker (1,024).
- Pattern classes are ASCII-only (`(?-u:\b)`, explicit classes), which the `regex` crate runs in
  linear time, so a hostile string cannot stall redaction.

Not in this stage: the transports and the asynchronous worker (Stage 3), and the CLI commands
beyond `verify` (Stage 4). Metrics, NoSQL / queue document shapes and the OTLP exporters'
span assembly have no corpus group yet.
