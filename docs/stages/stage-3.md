# Stage 3 — runtime: worker and transports

Goal: the threads, files and sockets that sit around the pure core. Behaviour is in
`docs/spec/05-runtime-and-transports.md`; the crate is `crates/logquill-runtime`.

## Status: done

| Piece | Where | Checked by |
|---|---|---|
| Bounded queue and one worker thread; `drop_oldest`, `drop_newest`, `block`; rate-limited drop warning; `drain`, idempotent `close`; a panicking task does not stop the worker | `worker.rs` | `tests/runtime.rs`, Miri |
| Restart after `fork` (D25): the worker notices a new process id and starts fresh state | `worker.rs` | simulated fork in `tests/runtime.rs`; a real `fork` in `logquill-runtime-checks/tests/fork.rs` |
| Transport contract and the three formatters | `transport.rs` | `tests/runtime.rs` |
| Console, rotating file (size rotation, `gz` / `zip` compression, Fernet encryption) | `console.rs`, `file.rs`, `fernet.rs` | `tests/runtime.rs`, `tests/files.rs`, `tests/fernet_fixture.json` |
| Batching base bounded by records and bytes; HTTP NDJSON and the Datadog, New Relic (429 pause), Elasticsearch and Application Insights sinks | `batching.rs`, `http.rs` | `tests/http.rs` |
| Syslog (RFC 5424), SQLite, OTLP/HTTP traces and logs | `syslog.rs`, `sqlite.rs`, `otlp.rs` | `tests/sinks.rs` |
| Slack, PagerDuty and email alerting; StatsD, Prometheus pushgateway and OTLP metrics | `alerts.rs`, `metrics.rs` | `tests/alerting.rs` |
| Level file and environment watchers; exit-time `close_all` and serverless `with_flush` | `watchers.rs`, `shutdown.rs` | `tests/lifecycle.rs` |
| Dispatcher: a failing or panicking transport never stops logging; `flush` does not close | `dispatcher.rs` | `tests/runtime.rs` |

Exit criteria:

- Burst-and-stall: 100,000 records into a stalled sink with a queue bound of 1,000 never exceed the bound
  (`tests/runtime.rs`); live memory growth is also capped at 8 MiB by a counting allocator.
- Allocation budget: at most 2 allocations per record on the dispatcher path (it is 1.00 today), asserted in
  `logquill-runtime-checks/tests/allocations.rs`, which runs in the CI `test` job. Timing is enforced as a ratio:
  emitting and delivering 50,000 records must take at most 12x serialising them with `serde_json`, measured in the
  same run (`logquill-runtime-checks/tests/timing.rs`, release mode). It is about 5x today. A ratio cancels out
  machine speed, which absolute times cannot.
- Real `fork` test: `logquill-runtime-checks` (a separate crate, because the workspace forbids `unsafe`).
- Miri on the queue: the `miri` CI job runs the queue and worker tests. The 100,000-record burst test is
  excluded from it as too slow.

Fork and a busy transport: the child inherits the transport lock if the parent's worker was mid-write, and
std cannot release a lock held by a thread that no longer exists. So a host must call `flush` before forking
(Python: `os.register_at_fork(before=...)`); `tests/fork.rs` covers a drained fork with a slow transport. An
undrained mid-write fork is pinned in the same test: the child's `flush` returns `false` at its timeout instead
of hanging. Fixing that fully would need `unsafe`, which the runtime forbids.

Found on the way: `Dispatcher::flush` and `close` took the transport lock even after the drain timed out, so a
stuck sink made them ignore their timeout. They now return `false` at the timeout and leave the transports to the
worker; a later `close` finishes the job (`tests/runtime.rs`).
