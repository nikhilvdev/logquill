# Decisions: where Python and JS differ

Each row is a real difference found in the code. "Recommend" is the proposed choice
for the Rust-based 3.0; change it here before building if you disagree.

**How the choice is made:** Python 2.0 is the baseline because it is far ahead of JS; JS
is followed only where it is the better practice, and defects in either are fixed. The
full rule, the list of what JS must gain and the list of what everyone adopts from JS are
in `09-parity-and-gap-closing.md`. Rows where the recommendation is JS's behaviour: D2, D9,
D11, D13, D15, D17, D18, D30 (and D20–D27 are fixes for both). Items marked
**fix** are defects in both or one side that the new code must not reproduce.

| ID | Topic | Python (P) | JavaScript (J) | Recommend |
|---|---|---|---|---|
| D1 | Does `child()` inherit plugins? | No. Transports, level and content policy are inherited; `fixed_meta` is added via a `ContextPlugin`. A child can `.use(RunPlugin())` without touching the parent. | Yes: plugins are copied (the same instances are shared, so a `RunPlugin` counter is shared). Base `meta` is merged. | Do not inherit by default. Plugins that declare themselves inheritable (redaction, PII, the tamper chain, which must share one chain) are inherited. `AuditLogger.child` already needs this. |
| D2 | Default dispatch | Synchronous on the caller's thread; `async_dispatch=True` opts into a queue and worker thread. | Always queued and drained on `setImmediate`/microtask. | Queued by default in every language, with an explicit synchronous option for tests and short scripts. This is a behaviour change for Python. |
| D3 | Placeholder for content when policy is `off` | `"<content capture is off — see Logger(content_policy=...)>"` | `"<content capture is off — see Logger's contentPolicy option>"` | One language-neutral string, fixed in a golden fixture: `"<content capture is off — set the logger's content policy to capture it>"`. |
| D4 | Float output in JSON lines | `12.0` stays `12.0` | `12.0` prints as `12` | Records compare by parsed value, not by text. For the hash chain, preserve whether a number was written as an integer or a float (see 03). |
| D5 | `run_id` default | `uuid4().hex` (32 hex, no dashes) | `randomUUID()` (with dashes) | 32 hex without dashes. Consumers must treat run ids as opaque. |
| D6 | PII patterns | 12 patterns (email, ssn, credit_card, phone, openai/anthropic keys, AWS key id, GitHub token, Slack token, JWT, private-key block, bearer token) | 4 (email, ssn, creditCard, phone) | Python's 12, snake_case names. |
| D7 | Queue drop warning interval | 60 s | 5 s (`warnIntervalMs`) | 60 s default, configurable. |
| D8 | Level-change signal | `SIGUSR1`, env `LOGQUILL_LEVEL` | `SIGUSR2` (Node reserves `SIGUSR1`), same env; throws on Windows | Keep each language's default; same env variable; the core only supplies "apply this string as a level". |
| D9 | Console colour default | Always on | On unless `NO_COLOR` is set | On only for a terminal and when `NO_COLOR` is unset, in every language. |
| D10 | HTTP transport bounds | `batch_size=50`, `max_bytes=1_000_000`, `timeout=10`; drops a failed batch with a logged error | `batchSize=50` only; no byte bound; no timeout option | Python's. |
| D11 | Elasticsearch auth | none | `apiKey` → header `Authorization: ApiKey …` | Add API key and basic auth. |
| D12 | App Insights | Posts envelopes to `https://dc.services.visualstudio.com/v2/track` using an instrumentation key; ignores the formatter | Uses the `applicationinsights` SDK with a connection string; message is the formatted record | Native HTTP envelopes (Python's), accepting a connection string. |
| D13 | Redis stream name | `"logs"` | `"logquill:logs"` | `"logquill:logs"`. |
| D14 | RabbitMQ default URL | `amqp://guest:guest@localhost:5672/%2F` | `amqp://localhost` | Python's explicit form. |
| D15 | SQS chunking | Sequential chunks of 10 | Chunks of 10 sent in parallel | Parallel with a small bound. |
| D16 | Caller info shape | `module`, `function`, `line`, `file` | `function`, `file`, `line`, `column` | Host-defined; both stay valid under `meta.caller`. |
| D17 | Syslog over TCP | No cap on pending data | Drops writes when pending bytes would exceed 1 MiB | Adopt the cap. |
| D18 | Legacy meta keys | 1.x already snake_case | `parse_record` renames camelCase keys of a record with no `schema_version` (`runId`→`run_id`, `spanId`→`span_id`, `parentSpanId`→`parent_span_id`, `traceId`→`trace_id`, `durationMs`→`duration_ms`, `prevHash`→`prev_hash`) | Keep the rename for legacy records in the core; harmless for Python's. |
| D19 | NaN / Infinity in canonical JSON | Emits `NaN`, `Infinity`, `-Infinity` | Emits `null` | Python's, so existing Python chains still verify. |
| D20 | Defect: redaction failure | A plugin whose `before_log` raises is skipped and the record continues unredacted | Same | **fix**: a redaction plugin that fails drops the record. |
| D21 | Defect: PII on non-JSON values | Objects/exceptions in `meta` are stringified later with `default=str`, after redaction | Same (`JSON.stringify`) | **fix**: scan the string form of every non-JSON leaf. |
| D22 | Defect: chain under concurrency | Hash computed on the caller's thread, written later | Single-threaded, but queued | **fix**: chain at write time, one record at a time. |
| D23 | Defect: unbounded id sets | `SamplingPlugin._elevated`, `FlightRecorderPlugin._flushed` never evicted | `SamplingPlugin.elevated` never evicted | **fix**: bound both (evict oldest at `max_traces` / `max_runs`). |
| D24 | Defect: stdlib bridge loop | The `LogQuillHandler` forwards LogQuill's own diagnostics | n/a | **fix**: ignore records from the `logquill` logger. |
| D25 | Defect: worker after `fork` | Worker thread does not survive; queue never drains | n/a | **fix**: restart in the child. |
| D26 | Defect: failed rotation | File closed, never reopened | Same shape (`closeSync` then rename) | **fix**: reopen in a `finally`. |
| D27 | Defect: `RedactPlugin` depth | Top-level keys only | Top-level keys only | **fix**: recurse with a depth and cycle guard. |
| D28 | `span()` shape | `with logger.span(name, **meta)` context manager; emits on exit | `await logger.span(name, fn, options)` callback; emits on settle | Idiomatic per language; the emitted record is identical. |
| D29 | `llm_call` shape | keyword args `tokens_in=…` | camelCase options `tokensIn` | Idiomatic per language; wire names are snake_case. |
| D30 | Release gate | Tag runs build only; tests are a manual check | Tag runs lint, typecheck, tests, build, then publish | Tests in the release job, as JS does. |
| D31 | Non-finite numbers in JSON lines | `json.dumps` writes `NaN`/`Infinity` (not valid JSON) | `JSON.stringify` writes `null` | `null` (valid JSON). The hash chain still uses the Python words (D19). |
| D32 | Non-ASCII in JSON lines | Escaped as `\uXXXX` (stdlib `ensure_ascii`); the optional `orjson` backend writes UTF-8 | UTF-8 | UTF-8. Readers must compare parsed values. The hash chain keeps ASCII-escaped canonical text (03). |
| D33 | Reading the SQLite table in the viewer | Rebuilds records with `schema_version "1.0"` and no `llm` (the table has no column for it) | n/a | Add an `llm` JSON column to the new SQLite sink so tokens and cost survive; keep reading old tables. |
| D34 | Sampling elevation writes straight to sinks | Bypasses later plugins and, when asynchronous, writes on the caller's thread | Same, on the queue's turn | Route elevated records through the same dispatch path (queue and chain) instead of writing directly. |
