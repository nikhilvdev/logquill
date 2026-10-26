# 02 — Plugins

Every plugin is attached with `logger.use(...)`, runs its `before_log` in registration
order, and (where it has one) its `after_log` after the transports. Defaults below are
the code's defaults.

## ContextPlugin

`ContextPlugin(**context)`. `record.meta = {**context, **record.meta}`: a key already in
the record's `meta` wins.

## RunPlugin

`RunPlugin(run_id=None)`. Default id: 32-hex UUID (see D5). Per record:
`meta.run_id` set only if absent; `meta.step` = this instance's counter (starting 0),
then increment. One instance = one run; a shared instance shares the counter.

## RateLimitPlugin

`RateLimitPlugin(max_records, per_seconds, key_func, max_keys=1000, clock)`.
`max_records < 1` → error `max_records must be at least 1, got <n>`; `per_seconds <= 0`
→ `per_seconds must be positive, got <n>`. Default key = `(logger, level)`. Per key a
fixed window: if no window or `now − start ≥ per_seconds` → new window `(now, 1)`, record
passes, evict the least-recently-used key while more than `max_keys`; else mark the key
most-recent, and if `count ≥ max_records` drop, otherwise count + 1 and pass. Clock is
monotonic (P `time.monotonic`, J `performance.now()/1000`).

## SamplingPlugin

`SamplingPlugin(rate, rng, trace_key="trace_id", elevate_at=ERROR, transports=None,
max_buffered_records=1000, max_traces=200)`. `rate` outside 0..1 → error `rate must be
between 0 and 1, got <n>`.

Without `transports`: keep iff `rng() < rate`.

With `transports` (tail-based):
1. `trace_id = meta[trace_key]` (absent counts as no trace).
2. If the trace is already elevated → pass.
3. `keep = rng() < rate`; `reached = level ≥ elevate_at`.
4. If a trace id exists and `reached` → **elevate**: mark elevated, remove its buffered
   records, write each straight to every transport (`transport.write(transport.format(r), r)`),
   pass this record.
5. Else if `keep` → pass.
6. Else if a trace id exists → buffer the record under it (moving the trace to
   most-recent; if a new trace and the buffer holds `max_traces`, evict the oldest
   trace; then while total buffered > `max_buffered_records` evict the oldest trace),
   and drop.
7. Else drop.

Buffered flushes bypass `before_log`/`after_log` of later plugins. **Defect (D23):** the
elevated set is never bounded; bound it to `max_traces`.

## AdaptiveSamplingPlugin (P)

`AdaptiveSamplingPlugin(base_rate=0.1, min_rate=0.01, max_rate=1.0, keep_at=ERROR,
slow_ms=1000.0, max_bytes_per_second=100_000, window_seconds=1.0, rng, clock)`. Rates
outside 0..1 → `<name> must be between 0 and 1, got <v>`; `min_rate > max_rate` →
`min_rate (<a>) must be <= max_rate (<b>)`.

Per record: roll the window if `clock() − window_start ≥ window_seconds`:
`demand = demand_bytes / max_bytes_per_second` (0 if the budget is 0); `demand < 0.5` →
`rate = min(rate × 1.2, max_rate)`; `demand > 0.9` → `rate = max(rate × 0.5, min_rate)`;
the new window starts at *now* with demand and emitted counters zero.
Always keep: level ≥ `keep_at`, or a span record (`meta.kind == "span"`) whose
`duration_ms` (a real number, not a bool) ≥ `slow_ms`. Otherwise drop if
`rng() ≥ rate`; add the record's compact-JSON byte length to `demand_bytes`; drop if
`emitted + size > max_bytes_per_second`; else add to `emitted` and pass. Size estimate:
`len(compact JSON, default=str, UTF-8)`, falling back to `len(str(record))`.

## FlightRecorderPlugin (P)

`FlightRecorderPlugin(transports, run_key="run_id", ship_at=INFO, flush_at=ERROR,
max_buffered_records=1000, max_runs=200)`.
Per record: no `meta[run_key]` → pass. Run already flushed → pass. Level ≥ `flush_at` →
mark flushed, write the run's buffered records straight to the transports, pass. Level ≥
`ship_at` → pass. Otherwise buffer under the run (same eviction rules as sampling) and
drop. **Defect (D23):** the flushed set is unbounded; bound it to `max_runs`.

## RunSummaryPlugin (P)

`RunSummaryPlugin(transports, run_key="run_id", logger_name="app.run_summary",
max_runs=1000)`; works in `after_log`. Per record with a `meta[run_key]`: get-or-create
the run's stats (evict the oldest run if at `max_runs`); `record_count += 1`;
level ≥ ERROR → `errors += 1`; integer `retry_count` (not bool) → `retries +=`;
`kind == "action"` with a string `tool` → `tool_calls += 1`; `llm` ints add to
`tokens_in`/`tokens_out`, a real `cost_usd` adds to `cost_usd`. A record with
`kind == "span"`, a string `span_id` and **no** `parent_span_id` key is the root span:
remember its id and `duration_ms` (float), delete the run's stats and emit the summary
record straight to the transports (INFO, logger `logger_name`, message `"run summary"`).
Summary `meta`: `kind="run_summary"`, `<run_key>`, `tokens_in`, `tokens_out`, `cost_usd`,
`tool_calls`, `retries`, `errors`, `record_count`, and when known `duration_ms` and
`parent_span_id` (the root span id). The summary itself is not run through later plugins.

## TraceContextPlugin

`TraceContextPlugin(trace_key="trace_id", traceparent=None)`. If `meta[trace_key]` is
set (not null) leave it. Otherwise resolve, first hit wins:
1. the active OpenTelemetry span's trace id if valid, as 32 lower-case hex (lookup is
   best-effort; missing OTel is not an error);
2. the constructor `traceparent`, then the context value set by `set_traceparent()`
   (isolated per task), parsed;
3. a fresh 32-hex id (16 random bytes).

Header parsing (a header that matches none is "no header"; the value is trimmed first):
- W3C `^[0-9a-f]{2}-(?P<trace_id>[0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$` → the trace id
- X-Ray (search, not anchored) `Root=1-(?P<time>[0-9a-f]{8})-(?P<rand>[0-9a-f]{24})` → time + rand
- GCP `^(?P<trace_id>[0-9a-f]{32})/\d+(;o=\d)?$` → the trace id

Known gap: an all-zero trace id or version `ff` is accepted by the W3C pattern.
`set_traceparent(value)` returns a token/restore function.

## RedactPlugin

`RedactPlugin(keys=<secret-class keys>, replacement="***", classes=None)`. Default keys
are the `secret` class: `password`, `token`, `secret`, `api_key`, `authorization`.
`classes` adds every key of the named classes (`secret`, `pii`, `content`). Matching is
case-insensitive on the key. Each matching top-level `meta` value becomes `replacement`.
`redact_local(name, text)` returns `replacement` when the variable name matches.
**Defect (D27):** only top-level keys are checked; recurse (depth 50, cycle guard) in
the new code. **Defect (D20):** a non-string key makes this raise in P (`key.lower()`);
the new code must not.

## PIIRedactPlugin

`PIIRedactPlugin(patterns=DEFAULT, replacement="***", use_presidio=False,
presidio_entities=None, presidio_language="en")`. Scans string **values** (any key),
recursively through dict/list/tuple, depth limit 50 (deeper values left as is), cycle
guard (a container seen on the current path is left as is). Patterns (Python regex
syntax; verbatim):

```
email             \b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b
ssn               \b\d{3}-\d{2}-\d{4}\b
credit_card       \b(?:\d[ -]?){13,16}\b
phone             \b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b
openai_api_key    \bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b
anthropic_api_key \bsk-ant-[A-Za-z0-9_-]{20,}\b
aws_access_key_id \b(?:AKIA|ASIA)[0-9A-Z]{16}\b
github_token      \bgh[pousr]_[A-Za-z0-9]{36,}\b
slack_token       \bxox[baprs]-[A-Za-z0-9-]{10,}\b
jwt               \beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b
private_key_block -----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY----- (dot matches newline)
bearer_token      \bBearer\s+[A-Za-z0-9\-_.]{20,}\b
```

(J has only email, ssn, creditCard, phone; see D6.) Replacement algorithm (P): find
matches **independently per pattern over the original text**; sort all spans by (start,
−end); sweep taking non-overlapping spans (a span starting before the last chosen end is
skipped); rebuild the text replacing chosen spans. (J applies patterns one after another
in place, which can leave a secret half-redacted; the new code uses the P algorithm.)
Presidio mode (P only, optional, ML dependency): runs analyzer + anonymizer on each
string. `redact_local` applies the same text redaction to a local's repr.
**Defect (D21):** non-JSON values are stringified after this plugin runs; scan their
string form.

## TamperEvidentPlugin

See `03-privacy-tracing-otel.md` for the canonical form and hash. Plugin behaviour:
`TamperEvidentPlugin(genesis_hash="0"*64)` keeps `last_hash`. Per record:
`prev = last_hash`; `digest = hash(record, prev)`; `record.meta = {**meta, prev_hash: prev,
hash: digest}`; `last_hash = digest`. Properties `head_hash`, method
`sign_head(key)`. Static `verify_chain(records, genesis_hash)`. **Defect (D22):** the
hash is computed on the calling thread and the record written later; chain where records
are written.

## AlertingPlugin and senders

`AlertingPlugin(threshold=ERROR, dedupe_window_seconds=300.0, dedupe_key, max_tracked_keys=500)`;
works in `after_log`. Default dedupe key = `"<LEVEL>:<logger>:<message>"`.
Algorithm: level below threshold → ignore. Existing window for the key → `count += 1`,
stop. No window and `max_tracked_keys` windows already open → ignore (alerting degrades,
logging does not). Otherwise open a window (timer = `dedupe_window`), send an alert now
with occurrences 1 on a background worker, never on the caller's thread. When the timer
fires: remove the window; if `count > 1` send one follow-up with the total count. A send
that raises goes to the plugin's `on_error`. `close()` cancels pending timers. (P spawns a
thread per alert; J does not await. The new code uses a bounded pool.)

Senders (each takes `record`, `occurrences`):
- **Slack** `SlackAlertPlugin(webhook_url, timeout=5.0)`: POST JSON
  `{"text": "[<LEVEL>] <logger>: <message>" + " (x<n>)" if n > 1}`; status ≥ 400 raises
  `SlackAlertPlugin: webhook returned HTTP <s> — check the webhook URL is still valid in Slack's app config`.
- **PagerDuty** `PagerDutyAlertPlugin(routing_key, timeout=5.0)`: POST to
  `https://events.pagerduty.com/v2/enqueue`: `{routing_key, event_action:"trigger",
  payload:{summary:"<logger>: <message>" + " (x<n>)", severity: ERROR→"error",
  FATAL→"critical" (default "error"), source:<logger>, timestamp, custom_details:
  {occurrences, **meta}}}`; status ≥ 400 raises `PagerDutyAlertPlugin: Events API returned
  HTTP <s> — check the routing key is a valid Events API v2 integration key`.
- **Email** `EmailAlertPlugin(smtp_host, smtp_port, from_addr, to_addrs, username,
  password, use_tls=True, timeout=10.0)`: subject `[<LEVEL>] <logger>` + ` (x<n>)`;
  plain-text body `"<message>\n\noccurrences: <n>\ntimestamp: <ts>\nmeta: <meta repr>"`;
  new SMTP connection per alert; STARTTLS when `use_tls`; login only if both
  credentials set.
- **Apprise** (P, optional library) `AppriseAlertPlugin(urls, title=None, apprise_client)`:
  body = message + ` (x<n>)`; title default `"[<LEVEL>] <logger>"`; notify type WARN→
  `warning`, ERROR/FATAL→`failure`, else `info`; a false return raises
  `AppriseAlertPlugin: Apprise reported that at least one notification failed to send …`;
  an unrecognised URL raises `ValueError` at construction. Calls are serialised.

## MetricsPlugin (P)

`MetricsPlugin(backend=OTel, error_metric="logquill.errors",
duration_metric="logquill.duration_ms", error_at=ERROR)`; works in `after_log`. Tags =
`{"logger": <logger>}`. Level ≥ `error_at` → `backend.increment(error_metric, tags)`.
A real-number `meta.duration_ms` (not bool) → `backend.observe(duration_metric, float(v),
tags)`. Backend protocol: `increment(name, *, tags)`, `observe(name, value, *, tags)`,
optional `close()`.

Backends:
- **OTel**: one counter and one histogram instrument per name, created lazily; `add(1)` /
  `record(value)` with `attributes=tags`; meter name default `"logquill"`.
- **StatsD** `(host="localhost", port=8125, prefix=None)`: UDP, fire and forget; counter
  `<name>:1|c`, histogram `<name>:<value>|h`, tags appended `|#key:value,key:value`; prefix
  `<prefix>.<name>`; a failed send is logged at debug and dropped.
- **Prometheus pushgateway** `(gateway_url, job_name="logquill", timeout=5.0)`: accumulate
  in memory (counters; histogram sum and count); `push()` sends PUT
  `<url>/metrics/job/<job>` with text lines `name{k="v",…} value`, histograms as
  `name_sum{…}` and `name_count{…}`; names are sanitised: every char outside
  `[a-zA-Z0-9_:]` becomes `_`, a leading digit gets a `_` prefix; labels sorted by key;
  totals are cumulative and not cleared; empty → no request; status ≥ 400 raises `…
  pushgateway returned HTTP <s> — check the gateway URL is reachable and correct`;
  `close()` pushes once.

## OtelSpanProcessor (J only)

`OtelSpanProcessor(log, {attributesKey="attributes"})`, duck-typed to an OTel span
processor. `onStart(span)`: `log.action(span.name, base_meta)`. `onEnd(span)`:
`duration_ms = seconds×1000 + nanos/1e6`; status code 2 (ERROR) →
`log.error(name, {..., error: status.message ?? "span ended with an error status"})`,
else `log.observation(name, ...)`. `base_meta`: `span_id` (the span's own id),
`parent_span_id` if present (`parentSpanContext.spanId`, fallback `parentSpanId`), and
`meta[attributesKey] = span.attributes` when non-empty. `forceFlush`/`shutdown` do nothing.
