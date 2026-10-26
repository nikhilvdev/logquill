# 01 — Records, levels and the Logger

## Levels

| Name | Weight |
|---|---|
| TRACE | 5 |
| DEBUG | 10 |
| INFO | 20 |
| WARN | 30 |
| ERROR | 40 |
| FATAL | 50 |

A record carries the **name** (upper case), never the weight. A level may be given as
a name (any case), a weight that is exactly one of the six, or the enum member.
Anything else is an error: `Unknown log level: <value>` (P: `ValueError`; a value of
the wrong type is a `TypeError`: `level must be int, str, or Level, got <type>`).
A record is dropped when `level < logger.level`.

## Record shape

JSON object, keys in this order:

```
schema_version  "2.0"
timestamp       "YYYY-MM-DDTHH:MM:SS.mmmZ"   UTC, exactly three fraction digits
level           name
logger          dotted logger name
message         string
meta            object (always present, may be empty)
llm             object (only on LLM-call records)
```

No other top-level keys. `meta` is free-form except the named keys in
`schema/record.schema.json` (that file is authoritative for names and types).

**Timestamp algorithm (P).** `now = time()`; `second = floor(now)`;
`µs = round((now − second) × 1e6)`; if `µs ≥ 1_000_000` then `second += 1; µs = 0`;
milliseconds = `µs ÷ 1000` truncated. Format `second` as `YYYY-MM-DDTHH:MM:SS` UTC and
append `.mmm` and `Z`. P caches the formatted whole-second prefix and reuses it until
the second changes. J uses `Date.toISOString()` (same shape).

## `parse_record(raw)` (reading another process's record)

Returns a new object; the input is not modified.

1. `raw` must be an object, else error `a log record must be a JSON object, got <type>`.
2. No `schema_version` → set `"1.0"` and mark the record legacy.
3. `schema_version` must be a string matching `^(\d+)\.(\d+)$`, else
   `schema_version must be a string like '2.0', got <v> — is this a LogQuill record?`.
4. Major version greater than 2 → `this record has schema_version <v>, newer than the
   '2.0' this logquill understands — upgrade logquill to read it`.
5. `timestamp`, `level`, `logger`, `message` must be strings, else
   `record field '<f>' must be a string, got <v>`.
6. `level` must be one of the six upper-case names, else
   `record level <v> isn't one of TRACE, DEBUG, INFO, WARN, ERROR, FATAL (upper case)`.
7. Missing `meta` → `{}`. `meta` must be an object, else
   `record field 'meta' must be an object, got <type>`. (J, legacy records only:
   rename the six camelCase keys listed in decision D18.)
8. `llm`, if present, must be an object, else
   `record field 'llm' must be an object, got <type>`.
9. Unknown keys are kept (a newer minor version passes through).

The fixtures `schema/golden_records.json` (27 valid, 39 invalid, 5 legacy, 11
rejected) are the executable form of this section.

## `llm` block

Keys: `model` (string), `tokens_in`, `tokens_out` (integers ≥ 0; booleans rejected),
`cost_usd`, `latency_ms` (numbers ≥ 0; booleans rejected; J also rejects non-finite),
`finish_reason` (string). Building the block:

- A field that is `None`/`undefined` is skipped.
- An invalid field is dropped with the warning
  `llm_call: ignoring <name>=<value> — expected <a non-negative integer | a non-negative number | a string>`;
  an unknown field name expects
  `one of model, tokens_in, tokens_out, cost_usd, latency_ms, finish_reason`.
- If nothing is left the record has no `llm` key at all.

## Logger options

| Option | P default | J default |
|---|---|---|
| `name` | required | required |
| `level` | INFO | INFO |
| `transports` | none | none |
| `plugins` | none (a plain function is wrapped as an anonymous plugin) | same |
| `content_policy` / `contentPolicy` | `off` | `off` |
| dispatch | synchronous; `async_dispatch=True` for a queue | always queued |
| queue size | `max_queue_size=10_000` | `queue.maxSize=10_000` |
| backpressure | `drop_oldest` (also `drop_newest`, `block`) | `dropOldest` (also `dropNewest`, `block`) |
| exit flush | `flush_at_exit=True` | none (opt in with `installShutdownHandlers`) |
| fixed `meta` | `child(name, **fixed_meta)` | `meta` option and `child(name, meta)` |

`set_level` accepts the same inputs as a level. `use(plugin)` returns the logger.

## The log pipeline (normative order)

For a call at `level` with message and call-site `meta`:

1. If `level < logger.level` or the logger name is disabled (see Toggle), return
   nothing. Nothing else has run: no plugin, no lazy callable.
2. If lazy (see `opt`), replace every callable value in `meta` by its result; a
   callable that raises becomes `"<lazy value raised <ErrorType>: <message>>"`.
3. Retry tracking (below) may add `meta.retry_count`.
4. If a depth was requested, add `meta.caller` unless already present.
5. Pop `diagnose` (truthy → also set `exc_info` to true if absent). If `exc_info` is
   present, pop it and format a traceback into `meta.stack`. A malformed `exc_info`
   logs a warning and is ignored. (J: if `meta.err` is an `Error`, remove it and set
   `meta.stack = err.stack` or `"<name>: <message>"`; a non-Error is left alone.)
6. Build the record (schema version, timestamp, level name, logger, message, meta,
   `llm` when given).
7. Merge bound context under call-site values. Priority, highest first: call-site
   `meta`, bound context, fixed/base `meta`.
8. If a span is current, `meta.parent_span_id` is set to it **unless already present**.
9. Apply the content policy to `meta` (before any plugin).
10. For each plugin in registration order call `before_log(record)`:
    - an exception → call the plugin's `on_error(exc, record)` (itself guarded) and
      continue with the unchanged record (**Defect for redactors**, see D20);
    - a null result → drop the record, stop;
    - otherwise the result replaces the record.
11. Dispatch (queued if asynchronous): for each transport, `write(format(record),
    record)`; an exception is logged and does not stop the other transports. Then
    each plugin's `after_log(record)`; an exception goes to that plugin's `on_error`.
12. Return the record (null if filtered or dropped).

Plugins and transports receive the same mutable record; no deep copy is made. This is
a documented contract.

## Convenience methods

| Method | Level | Added to `meta` |
|---|---|---|
| `trace` `debug` `info` `warn` `error` `fatal` | as named | — |
| `thought` | INFO | `kind="thought"` |
| `action` | INFO | `kind="action"` |
| `observation` | INFO | `kind="observation"` |
| `decision` | INFO | `kind="decision"` |
| `llm_call(message="llm_call", model, tokens_in, tokens_out, cost_usd, latency_ms, finish_reason, **meta)` | INFO | `kind="action"` and the `llm` block |

The `kind` is placed first, so a caller's own `kind` wins. P's `message` argument is
positional-only so a `meta` key named `message` cannot collide with it.

## Spans

`span(name, span_id=None, parent_span_id=None, capture_state=None, **meta)`.

- Id: 16 lower-case hex characters (P: first 16 of `uuid4().hex`; J: 8 random bytes).
  An explicit `span_id` is adopted unchanged.
- On entry: snapshot state (if `capture_state`), make this span current, start a
  monotonic timer.
- On exit emit one record with message = `name`, level INFO (ERROR if the block
  raised), and `meta` in this order: `span_id`, `duration_ms` (milliseconds rounded
  to 3 decimals), then the caller's `meta`; then `parent_span_id` only if explicitly
  given; `kind="span"` unless the caller set `kind`; `state_diff` unless the caller
  set it; `error="<ErrorType>: <message>"` when it raised. Auto-nesting adds
  `parent_span_id` in pipeline step 8, not here.
- The exception propagates unchanged.

**State diff.** Snapshots are deep copies (P `deepcopy`, J `structuredClone`); if the
callable raises or the value cannot be copied there is no diff and no error. If both
snapshots are objects: let `changed` = keys present in only one or whose values
differ; if none → no diff; else `{"before": {changed keys that exist before},
"after": {changed keys that exist after}}`. Otherwise `{"before": b, "after": a}`
if they differ. The diff is a **content field** (policy applies).

## Retry tracking

Per logger, bounded to 1024 entries, oldest evicted first. Only records whose
`meta.tool` is a non-empty string take part. Key = (current span id or `""`, tool,
`meta.tool_call_id` if a string else `""`).

- `kind == "action"`: attempt count for the key (first = 0, then 1, 2, …); if the
  count is above 0 set `meta.retry_count` unless already present.
- `kind == "observation"`, level below ERROR and no `error` key: forget the key.

## Toggle (`disable` / `enable`)

A process-wide map name → bool, replaced copy-on-write. `disable(name="")` /
`enable(name="")`; a non-string name is an error (`disable()/enable() take a logger
name such as __name__, got <type>`). `is_enabled(name)`: with no rules → true;
otherwise look up `name`, then each parent by stripping the last `.segment`, then `""`;
the first rule found wins; none → true. `"mylib"` covers `"mylib"` and `"mylib.http"`,
not `"mylib2"`. A `_reset` exists for tests.

## `opt(lazy=False, depth=None)`

Returns a view with the same logging methods. Cached per `(lazy, depth)`, at most 32
cached views (beyond that, a fresh uncached view). `depth` must be a non-negative
integer (P: `opt(depth=...) must be >= 0, got <n>`; J: `opt({ depth }) must be a
non-negative integer, got <n>`). `meta.caller`: P `{module, function, line, file}`
(frame `3 + depth` above the helper); J `{function, file, line, column}` parsed from
the V8 or Gecko stack line `4 + depth`; `function` is `"<anonymous>"` when unnamed.
No caller is recorded if the stack is not that deep.

## Exceptions and `diagnose` (P)

`exc_info` accepts `True` (the exception being handled; nothing if none), an exception
instance, or a `(type, value, traceback)` tuple; falsy means nothing. Output is the
standard formatted traceback text.

`diagnose=True` writes locals under each frame. Rules: a one-time warning
`diagnose=True writes local variable values into log records, which can leak sensitive
data (passwords, tokens, personal data) — keep it off in production, and use
RedactPlugin/PIIRedactPlugin if you must run it there`; for every frame of the
exception and every chained exception (cause, context, exception-group members, cycle
guarded): skip names starting `__` and reprs starting `<module `, `<function `,
`<class `, `<built-in `, `<bound method `; pass each `(name, repr)` through every
plugin's `redact_local` in order (a plugin that raises yields `"<redaction failed>"`);
cut each repr at 200 characters adding `...`. If capturing fails, fall back to the plain
traceback (which shows no values).

## Context

`bind_context(**values)` merges onto the current context for the duration of a block
(inner wins over outer); exit restores exactly the previous context. Isolated per
thread/async task (P `ContextVar`, J `AsyncLocalStorage`; the browser build uses a
plain stack that is **not** isolated across concurrent awaits). `current_context()`
returns `{}` outside any block. Values bound by `inbound()` (MCP) use the same
mechanism under the key `mcp`.

## Plugin protocol

`before_log(record) → record | null`, `after_log(record)`, `on_error(error, record)`,
and (P) `redact_local(name, text) → text`. A plain function is wrapped as a plugin
whose only hook is `before_log`. All hooks default to no-ops.

## `child(name, **fixed_meta)`

Name = `<parent>.<name>`. See decision D1 for plugin inheritance. In both languages the
child shares the parent's transport instances and queue/worker, inherits the level
(copied at creation) and the content policy. Closing either closes the shared
transports.

## `flush` / `close`

P `flush(timeout=None) → bool`: wait for the queue to drain, then call each
transport's `flush()`; safe to repeat; returns whether the queue drained in time (true
if not asynchronous). `flush_async` awaits the same in an executor. `close(timeout=5.0)`:
stop and drain the worker, then `close()` every transport and mark it closed so the exit
hook does not close it twice.
J `flush()`: wait for queued tasks. `close()`: flush, force every flushable transport to
send its buffer and wait, then close each transport.

## `AuditLogger` (P)

A `Logger` with `content_policy="off"` and plugins `[RedactPlugin(), PIIRedactPlugin(),
TamperEvidentPlugin(), *extra_plugins]`, plus `head_hash` and `sign_head(key)`.
`child()` returns an `AuditLogger` that **shares the same `TamperEvidentPlugin`** (one
chain across the family), gets fresh `RedactPlugin`/`PIIRedactPlugin` instances, drops
`extra_plugins`, adds fixed meta via `ContextPlugin`, and shares the worker.
