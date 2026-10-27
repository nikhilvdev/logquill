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
  emitting and delivering 50,000 records must take at most 40x serialising them with `serde_json`, measured in the
  same run (`logquill-runtime-checks/tests/timing.rs`, release mode). The ratio is about 5x on a laptop and 14x on
  a 2-core CI runner, because producer and worker contend on the queue lock and that depends on core count. So
  the budget is coarse: it catches order-of-magnitude regressions, not small ones.
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

## Fixed after review

- The alert timer could miss a wake-up between its stop check and its wait, so `close` could wait up to an hour
  (and a new window's summary could be late). The timer now holds the dedupe lock from the check to the wait, and
  `close` sets the stop flag under the same lock.
- Syslog and StatsD looked up the server's name for every record. Both now look it up once and keep the address
  until a send fails; StatsD also waits 30 s before retrying a lookup that failed.
- Syslog TCP had no write timeout and retried a dead server for every record (5 s each). Writes now time out
  (`write_timeout`, default 5 s), and a failed lookup or connect is reported once, then records are dropped quietly
  for 1 s, doubling to 60 s (`connect_timeout`, default 5 s).

Also fixed from the review:

- `Retry-After` is capped at one hour, and `inf` or `NaN` waits the default 60 s, so one bad header cannot pause
  New Relic sends for good.
- Datadog always sends JSON objects, so a text or logfmt formatter no longer produces an invalid array. (Application
  Insights was not affected: it builds its envelopes from the record.)
- The email sender refuses to send a username and password without TLS (`use_tls = false`).
- The Pushgateway and OTLP metrics backends keep at most `max_series` series (default 1000); samples for further
  series are dropped and counted (`dropped_samples()`). A logger name made per request can no longer grow them forever.
- A panic in a level-watcher callback is reported and the watcher keeps running.
- A SQLite database file is opened in WAL mode (best effort) so the viewer can read while the transport writes.

Nothing from the review is open.
