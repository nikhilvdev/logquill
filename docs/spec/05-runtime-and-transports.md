# 05 — Runtime and transports

## Transport contract

`format(record) → string` (via the transport's formatter), `write(formatted, record)`,
optional `flush()` (send buffered data now, release nothing) and `close()`. `write` must
not raise for a single bad record. The logger catches and logs anything that does.

## Dispatch queue / worker

Bounded FIFO of work items run by one background worker. Options: max size (default
10 000), policy:
- `drop_oldest` (default): evict the oldest item, enqueue the new one.
- `drop_newest`: discard the new item.
- `block`: P — the producer waits until there is space (or the worker is closed); J — the
  task runs inline on the caller's stack.
A drop produces a warning, rate-limited (P once per 60 s, J once per 5 s with the number of
drops since; D7): `AsyncWorker: queue full at max_queue_size=<n>, dropping records under
backpressure=<policy> — the consumer (a transport) isn't keeping up, or max_queue_size is
set too low for this burst rate` (J: `DispatchQueue: dropped <n> record(s) — queue exceeded
its configured size under the "<policy>" backpressure policy`). `max_queue_size < 1` →
error; unknown policy → `backpressure must be one of (…), got <v>`.

- `submit` after close is silently dropped.
- A work item that raises is logged and does not stop the worker.
- `drain(timeout)`: wait until every item submitted so far finished; returns whether it
  did in time (`None` = wait forever). `close(timeout=5.0)`: drain, mark closed, wake
  waiters, join the thread (1 s); idempotent; returns whether the drain finished.
- The worker is a daemon thread. **Defect (D25):** it must be restarted after `fork`.

## Exit and shutdown

P: a process-wide registry (weak references) of loggers; one exit hook installed on first
use; at exit it closes each distinct worker with a 5 s timeout (warning `logquill: 5.0s
wasn't enough to flush every queued record at exit — a transport is slow or unreachable;
call logger.close(timeout=...) yourself with a longer timeout if these records matter`),
then closes each distinct transport once (skipping those already closed explicitly), never
raising. Not run on `SIGKILL`/`os._exit`/an unhandled `SIGTERM`. J: `installShutdownHandlers(logger,
{signals=["SIGTERM","SIGINT"]})` runs `logger.close()` once on `beforeExit` or a signal and
calls `process.exit(0)` after a signal; returns a function that removes the listeners.

## Serverless

`with_lambda(loggers, timeout=5.0)` (aliases `with_cloud_function`, `with_azure_function`;
J `withFlush`/`withLambda`/`withCloudFunction`/`withAzureFunction`): wraps a handler
(sync or async) and, in a `finally`, flushes the logger(s) — **flush, not close**, because
a warm container reuses them. J additionally forces every flushable transport to send its
buffer. Accepts one logger or a list (P).

## Batching transports

Common base for sinks that send in batches. Options: `max_records` (default 100),
`max_bytes` (default 1 000 000). Each write converts the record to an item, adds its
estimated size, and flushes when **either** bound is reached. `flush()` swaps the buffer
out under a lock (held only for the swap, never while sending), does nothing if empty, and
sends the batch; a send failure is logged and the batch dropped (no retry); the send is
never called with an empty batch. `close()` flushes. Size estimate: P compact JSON UTF-8
length of the item (SQL rows: `len(message) + len(meta text) + 96`; graph rows:
JSON length of the props + key length + 32); J `JSON.stringify(item).length`.

## HTTP transport

`HTTPTransport(url, batch_size=50, max_bytes=1_000_000, timeout=10, backend, sender)`. Buffers
**formatted lines**; flush on count or bytes; POST body = lines joined by `\n`, header
`Content-Type: application/x-ndjson`. A failure is logged (`HTTPTransport: couldn't deliver
<n> log record(s) to <url> — check the URL is reachable and accepts POSTed NDJSON, or raise
`timeout`; those records were dropped`) and the batch dropped. `close()` flushes and
closes the sender. An optional keep-alive sender reuses one connection (P `aiohttp`).

## File transport

`FileTransport(path, max_bytes=10 MiB, backup_count=5, encrypt_key=None, compression=None)`.
Creates parent directories; opens for append. Each write appends the line plus `\n`
(optionally Fernet-encrypted first), flushes, then rotates if `max_bytes > 0` and the file
size ≥ `max_bytes`. Writes are serialised by a lock (so rotation cannot race a write).

Rotation: close the file; if `backup_count > 0`, for `i` from `backup_count−1` down to 1
rename `path.i` → `path.(i+1)` (missing sources skipped; an existing destination is
replaced), then move `path` to `path.1`; if `backup_count == 0`, delete `path`; reopen
`path`. **Compression (J, `gz` only):** backups are named with a `.gz` suffix and the
rotated file is gzipped on rotation; the new code supports `gz` and `zip` in both. **Defect
(D26):** reopen the file in a `finally` so a failed rename cannot leave the transport closed.

Encryption: a Fernet token per line (AES-128-CBC with HMAC-SHA256): key = 32 bytes
URL-safe base64 (with padding), first 16 bytes signing, last 16 AES; token = base64url(
`0x80` ‖ 8-byte big-endian seconds ‖ 16-byte IV ‖ ciphertext ‖ 32-byte HMAC over the
preceding bytes), padded base64; PKCS7 padding. A bad key fails at construction (P `Fernet
key` not valid; J `Fernet key must be 32 url-safe base64-encoded bytes — generate one with
generateFernetKey() (or Python's cryptography.fernet.Fernet.generate_key())`). Helpers:
generate key, decrypt (error texts `Not a Fernet token — was this line written with
encryptKey?` / `Fernet token failed verification — wrong key, or the line was edited`).
Output is interoperable between the two languages.

## Console transport

Info/debug/trace/warn → stdout; ERROR and FATAL → stderr. Optional ANSI colour wraps the
formatted line: TRACE `\x1b[90m`, DEBUG `\x1b[36m`, INFO `\x1b[32m`, WARN `\x1b[33m`,
ERROR `\x1b[31m`, FATAL `\x1b[35m`, reset `\x1b[0m`. One write + flush per record.
Streams are injectable. Colour default: see D9.

## Syslog transport

RFC 5424: `<PRI>1 <timestamp> <hostname> <app-name> <pid> - - <formatted>`; PRI =
`facility × 8 + severity`; facility default 1 (user). Severity: TRACE and DEBUG 7, INFO 6,
WARN 4, ERROR 3, FATAL 2. App name = `app_name` or the logger name. UDP (default): one
datagram per record. TCP: connect lazily, newline-framed (RFC 6587 non-transparent), J drops
a write when pending bytes would exceed 1 MiB (D17). Host default `localhost`, port 514;
protocol other than `udp`/`tcp` → `SyslogTransport: protocol must be 'udp' or 'tcp', got
<v>`. Not batched.

## HTTP-based sinks (move to the Rust runtime)

All batch by the common rules above (defaults 100 records / 1 MB).

- **Datadog.** URL `https://http-intake.logs.<site>/api/v2/logs`, `site` default
  `datadoghq.com` (e.g. `datadoghq.eu`, `us3.datadoghq.com`, `us5.datadoghq.com`,
  `ap1.datadoghq.com`). POST body = `[` + formatted records joined by `,` + `]`; headers
  `Content-Type: application/json`, `DD-API-KEY: <key>`; timeout 10 s. Error: `DatadogTransport:
  request to <url> failed with status <s> — check the API key and site region`.
- **New Relic.** `region` `US` → `https://log-api.newrelic.com/log/v1`, `EU` →
  `https://log-api.eu.newrelic.com/log/v1`. Body = gzip of the compact JSON **array** of
  records with `meta.eventType` removed (New Relic reserves it); headers
  `Content-Type: application/json`, `Content-Encoding: gzip`, `Api-Key: <licence key>`.
  Response 429: set `paused_until = now + Retry-After` (seconds, or an HTTP date; missing or
  unparseable → 60 s); until then **drop** (do not requeue) batches, logging `NewRelicTransport:
  sends paused until <time> after a 429 rate-limit response — skipping this batch rather than
  making a doomed request`. Other non-OK status → error `NewRelicTransport: request to <url>
  failed with status <s> — check the license key and region`. Clock injectable.
- **Elasticsearch.** `_bulk` endpoint `<url>/_bulk` (trailing slashes trimmed); body = for
  each record an action line `{"index":{"_index":"<index>"}}` (index default `logs`) then the
  ECS document (see 03), all joined by `\n` plus a final `\n`; header `Content-Type:
  application/x-ndjson`. The formatter is ignored (J warns when one is given). Failure:
  `ElasticsearchTransport: request to <url> failed with status <s> — check the URL and index`.
  J supports `apiKey` (`Authorization: ApiKey …`); see D11.
- **Application Insights (P).** POST newline-delimited envelopes to
  `https://dc.services.visualstudio.com/v2/track` (`Content-Type: application/x-json-stream`):
  `{"name":"Microsoft.ApplicationInsights.Trace","time":<timestamp>,"iKey":<key>,"data":
  {"baseType":"MessageData","baseData":{"ver":2,"message":<message>,"severityLevel":<n>,
  "properties":{<every meta key>: <str(value)>}}}}`. Severity: TRACE/DEBUG 0, INFO 1, WARN 2,
  ERROR 3, FATAL 4. (J uses an SDK client; D12.)
- **OTLP** — see 03.

## SQL transports

Row (fixed schema): `timestamp`, `level`, `logger`, `message`, `meta` (compact JSON text),
`run_id`, `span_id`, `parent_span_id`, `trace_id` (each a string from `meta` or null).
Table name default `logs`; P validates it against `[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?`
(error `<Class>: table_name <v> is not a plain SQL identifier — use letters, digits and
underscores (optionally schema.table)`); J does not validate. `ensure_schema=False`
(dev/test only): when true the DDL runs once before the first batch; the "done" flag is set
before running it so a re-entrant flush cannot run it twice. Production schema is the
caller's responsibility. One multi-row `INSERT` per batch, never one per record; commit after.

DDL:
- Generic/SQLite: `CREATE TABLE IF NOT EXISTS <t> (id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL, level TEXT NOT NULL, logger TEXT NOT NULL, message TEXT NOT NULL,
  meta TEXT NOT NULL, run_id TEXT, span_id TEXT, parent_span_id TEXT, trace_id TEXT)`
- Postgres: `id SERIAL PRIMARY KEY`, `meta JSONB NOT NULL`, rest as above; insert uses
  `%s` placeholders with `%s::jsonb` for `meta`.
- MySQL: `id INT AUTO_INCREMENT PRIMARY KEY`, `level VARCHAR(16) NOT NULL`, `logger TEXT`,
  `meta JSON NOT NULL`; `%s` placeholders.
- SQLite: `?` placeholders via `executemany`; default file `:memory:`.
Connection options: Postgres `dsn`; MySQL `host=localhost, port=3306, user=root,
password="", database=logquill`; an injected connection is never closed by the transport.

## NoSQL transports

- **MongoDB.** `insert_many` of the records as documents; defaults uri
  `mongodb://localhost:27017`, database `logquill`, collection `logs`.
- **Redis stream.** One `XADD` per record to the stream (P default `logs`, J
  `logquill:logs`; D13) with fields `timestamp`, `level`, `logger`, `message`, `meta`
  (compact JSON text). A fast buffer, not durable storage.
- **DynamoDB.** Item: partition key `run_id` = `meta.run_id`, else `meta.trace_id`, else the
  logger name; sort key `timestamp`; plus `level`, `logger`, `message`, `meta`, and
  `span_id`/`parent_span_id`/`trace_id` when strings. Batch writes chunk to 25 items (J
  explicit; P relies on the SDK's batch writer). Table default `logs`.

## Queue transports

Always batched, never one publish per log call; `topic` is the queue/topic identifier.
- **Kafka.** One message per record (compact JSON), key = `meta.run_id` else `meta.trace_id`
  else none (keeps a run on one partition); flush the producer after the batch. Default
  bootstrap `localhost:9092`.
- **SQS.** `SendMessageBatch` capped at **10** messages per call; entries `{Id: "<index>",
  MessageBody: compact JSON}`; `topic` is the queue URL.
- **Pub/Sub.** Publish each record's JSON to the full topic path, then wait for every
  publish future.
- **RabbitMQ.** One `basic_publish` per record on the default exchange with routing key =
  topic; declares the queue when it connects itself; default URL in D14.

## Cloud SDK sinks

- **CloudWatch.** Events `{timestamp: epoch ms of the record, message: formatted}` sorted
  by timestamp (the API requires chronological order) per `PutLogEvents` for the group and
  stream.
- **Cloud Logging.** Severity: TRACE/DEBUG `DEBUG`, INFO `INFO`, WARN `WARNING`, ERROR
  `ERROR`, FATAL `CRITICAL`. P: one structured entry per record (the record dict); J: batch
  of `{severity, timestamp, jsonPayload}` where the payload is the formatted record parsed.
  Default log name `logquill`.

## Graph transports

Shared Cypher (one multi-row statement per batch; verbatim):

```
UNWIND $rows AS row MERGE (n:LogRecord {key: row.key}) SET n += row.props
FOREACH (_ IN CASE WHEN row.session_key IS NULL THEN [] ELSE [1] END |
  MERGE (s:Session {key: row.session_key}) MERGE (s)-[:HAS_RECORD]->(n))
FOREACH (_ IN CASE WHEN row.parent_key IS NULL THEN [] ELSE [1] END |
  MERGE (p:LogRecord {key: row.parent_key}) MERGE (p)-[:PARENT_OF]->(n))
```

Row: `key` = `meta.span_id` when a non-empty string, else a fresh `leaf-<uuid hex>` (so
re-delivery of a span merges; plain lines do not); `session_key` = `meta[run_key]` else
`meta[trace_key]` (`run_key="run_id"`, `trace_key="trace_id"`), else none; `parent_key` =
`meta.parent_span_id` unless empty or equal to the key; `props` = `timestamp`, `level`,
`logger`, `message`, `meta` (compact JSON) plus `run_id`, `trace_id`, `span_id`,
`parent_span_id` when non-empty strings. Backends differ only in executing it: Neo4j
(`bolt://localhost:7687`, optional auth/database, session per query), FalkorDB (host
`localhost`, port 6379, graph `logquill`, `select_graph().query`), Neptune (`neptunedata`
openCypher with the parameters JSON-encoded as a string). Kuzu was skipped (upstream
archived).

## Memory budgets

See `08-tests-benchmarks-ci.md`.
