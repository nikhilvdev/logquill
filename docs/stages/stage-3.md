# Stage 3 — runtime: worker and transports

Goal: the threads, files and sockets that sit around the pure core. Behaviour is in
`docs/spec/05-runtime-and-transports.md`; the crate is `crates/logquill-runtime`.

## Status: started

Done in the first slice (std only, no new dependencies):

| Piece | Where | Checked by |
|---|---|---|
| Bounded queue and one worker thread; `drop_oldest`, `drop_newest`, `block`; rate-limited drop warning; `drain`, idempotent `close`; a panicking task does not stop the worker | `worker.rs` | `tests/runtime.rs` |
| Restart after `fork` (D25): the worker notices a new process id and starts fresh state, because the parent's thread and lock do not exist in the child | `worker.rs` | simulated fork test |
| The transport contract and the three formatters | `transport.rs` | — |
| Console: stdout / stderr split, ANSI colour, injectable streams, `NO_COLOR` default (D9) | `console.rs` | `tests/runtime.rs` |
| Rotating file: size rotation, `backup_count`, parent directories, reopen after a failed rotation (D26) | `file.rs` | `tests/runtime.rs` |
| Batching base: bounded by records **and** bytes, never an empty send, a failed batch is dropped | `batching.rs` | `tests/runtime.rs` |
| Dispatcher: records to transports on the worker; a failing or panicking transport is reported and never stops logging; `flush` does not close | `dispatcher.rs` | `tests/runtime.rs` |
| Burst-and-stall: 100,000 records into a stalled sink with a queue bound of 1,000 — the queue never exceeds the bound, one warning is raised, and the newest 1,000 are delivered once the sink recovers | — | `tests/runtime.rs` |

Still to do, in this order:

1. File compression (`gz`, `zip`) and Fernet encryption (needs `flate2`, `zip` and AES / HMAC crates;
   the output must interoperate with the old Python and JavaScript files).
2. HTTP NDJSON transport and the four HTTP cloud sinks (Datadog, New Relic with its 429 pause,
   Elasticsearch `_bulk`, Application Insights) — pick one small blocking HTTP client.
3. Syslog (RFC 5424, UDP / TCP), SQLite sink, OTLP/HTTP traces and logs (JSON encoding).
4. Slack, PagerDuty and email senders on one thread pool; StatsD and Prometheus pushgateway backends.
5. Level file and environment watchers.
6. Exit-time flush registry and the serverless flush helper.
7. Exit criteria not yet met: Miri / loom (or equivalent) on the queue; benchmark and allocation
   budgets enforced in CI; a real `fork` test (the workspace forbids `unsafe`, so it needs a
   separate test crate or a subprocess).
