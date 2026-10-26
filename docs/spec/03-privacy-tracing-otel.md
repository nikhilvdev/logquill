# 03 — Privacy, canonical JSON, tracing and OpenTelemetry

## Field classes

Registry (both languages, verbatim):

| Key | Class |
|---|---|
| `password`, `token`, `secret`, `api_key`, `authorization` | secret |
| `email`, `ssn`, `phone` | pii |
| `input_messages`, `output_messages`, `system_instructions`, `tool_arguments`, `tool_result`, `state_diff` | content |

`CONTENT_FIELDS` = the six `content` keys, in this order. `keys_in_class(class)` returns
the keys of a class. Class meanings: `secret` = always redact; `pii` = scanned by
pattern; `content` = governed by the content policy.

## Content policy

Values: `off` (default), `hash`, `truncate`, `full`; any other string is an error:
`content_policy must be one of ('off', 'hash', 'truncate', 'full'), got <v>` (P) /
`contentPolicy must be one of "off", "hash", "truncate", "full", got <v>` (J).

Applied **inside the logger, before any plugin**, to the six content keys when present
in `meta`:
- `full` → no change (and no work).
- `off` → value replaced by the placeholder (D3). Never dropped, so a reader can see
  capture was off.
- `hash` → `"sha256:" + hex(SHA-256(text))`, `text` = canonical JSON of the value (below),
  UTF-8 (P uses `surrogatepass`).
- `truncate` → if `len(text) ≤ 200` the `text` itself (a string); else
  `text[:200] + "...(truncated, <len> chars total)"`.

If canonical serialisation fails (a cycle, a raw object) the text is `str(value)`; if that
fails, `"<unrepresentable>"` (J). Never raises. Because the canonical text is ASCII-only,
lengths agree across languages.

## Canonical JSON (hashing and content-policy text)

Reproduces Python `json.dumps(value, sort_keys=True, default=str)`:

- Separators `", "` between items and `": "` between key and value (a space after both).
- Object keys sorted by Unicode code point (UTF-8 byte order is identical).
- Strings: `"` and `\` escaped; `\n \r \t \b \f` as those escapes; **every other character
  outside U+0020..U+007E** as `\uXXXX` (lower-case hex), so `U+007F` becomes `\u007f` and a
  non-BMP character becomes a surrogate pair `𝄞`.
- Integers: decimal, arbitrary precision. Floats: Python `repr` — shortest round-trip,
  exponent form below `1e-4` and from `1e16` with at least two exponent digits (`1e-05`,
  `1e+16`), `12.0` keeps its `.0`. Whether a number is an integer or a float must be
  preserved from the source (a JSON `12.0` is a float and prints `12.0`; `12` prints `12`).
  `NaN`, `Infinity`, `-Infinity` print as those words (D19).
- `true`, `false`, `null`. Tuples and lists are arrays.
- Non-JSON values: P `default=str` (the binding converts to strings before the core sees
  them). J renders `undefined`/functions/symbols as `null` or drops them from objects.
- A circular reference is an error (P `ValueError`, J `TypeError`).

Fixtures: `schema/golden_chain.json` (4 records covering non-ASCII, `U+007F`, an astral
character, control characters, small floats, an `llm` block).

## Hash chain

`GENESIS = "0" × 64`. For a record and `prev_hash`:

```
content = { timestamp, level, logger, message,
            meta: meta without the keys "hash" and "prev_hash" }
if schema_version is present and != "1.0": content.schema_version = schema_version
if llm is present:                          content.llm = llm
hash = hex_lower( SHA-256( utf8( prev_hash + canonical_json(content) ) ) )
```

(`"1.0"` is what the parser labels a record that had none, so a 1.x chain verifies the
same before and after parsing.) The record carries `meta.prev_hash` and `meta.hash`.

**Verify** (records in order, optional `genesis`): for each record, count it, then:
- no `meta.hash` → fail: `this record has no meta.hash — it was never chained, or that field was removed`;
- `meta.prev_hash` ≠ previous hash → fail: `meta.prev_hash doesn't match the previous record's hash — a line before this one was edited, removed, or reordered`;
- recomputed hash ≠ stored → fail: `this record's content doesn't match its own stored hash — this line was edited after being chained`.
Result: `ok`, `records_checked` (including the failing one), `broken_at` (1-based index),
`reason`, `head_hash` (the previous hash at the failure; the last hash on success, genesis
if empty; null for a failure on the first record).

**Signed head.** `sign_head(head_hash, key)` = lower-case hex HMAC-SHA256 of the UTF-8
head hash. `verify_head_signature` compares in constant time.
`verify_signed_chain(records, key, signature)` verifies the chain, then the signature; a
mismatch gives `broken_at = records_checked` and the reason `the chain is internally
consistent, but its head doesn't match the given signature — the file may have been
truncated, or replaced outright, after the signature was taken`. Store the signature
outside the log file.

## Trace and span ids

- Span id (16 hex): `span_id_for(value)`: if `value` matches `^[0-9a-f]{16}$` and is not
  all zero → itself; else the first 8 bytes of `SHA-256(utf8(value))` as hex (all zero
  becomes `…0001`). Upper-case hex is **not** treated as already valid.
- Trace id (32 hex): `trace_id_for(meta)`: `meta.trace_id` if it matches `^[0-9a-f]{32}$`
  and is not all zero; else if `meta.run_id` is a non-empty string, the first 16 bytes of
  `SHA-256(utf8("run:" + run_id))`; else none.
- `trace_id_from_parent(parent)`: first 16 bytes of `SHA-256(utf8("parent:" + parent))`.
- All-zero results become the value 1. Fixture: `schema/golden_otel_ids.json`
  (9 span-id, 10 trace-id, 3 parent cases).

## OpenTelemetry GenAI mapping (`semconv`)

Pinned to semantic-conventions **1.44.0**; the only place `gen_ai.*` names appear.
Two generations: `latest` and `legacy`; they differ only in three names.

| Concept | latest | legacy |
|---|---|---|
| operation | `gen_ai.operation.name` | same |
| provider | `gen_ai.provider.name` | `gen_ai.system` |
| request model | `gen_ai.request.model` | same |
| response model | `gen_ai.response.model` | same |
| input tokens | `gen_ai.usage.input_tokens` | `gen_ai.usage.prompt_tokens` |
| output tokens | `gen_ai.usage.output_tokens` | `gen_ai.usage.completion_tokens` |
| finish reasons | `gen_ai.response.finish_reasons` | same |
| agent name / id | `gen_ai.agent.name` / `gen_ai.agent.id` | same |
| conversation id | `gen_ai.conversation.id` | same |
| tool name / call id | `gen_ai.tool.name` / `gen_ai.tool.call.id` | same |
| input / output messages | `gen_ai.input.messages` / `gen_ai.output.messages` | same |

Own attributes use the prefix `logquill.`; `error.type` is used as is. Operation names:
`invoke_agent`, `execute_tool`, `chat`, `text_completion`. Unknown provider: `"unknown"`.

**Choosing a generation.** An explicit `latest`/`legacy` wins (anything else:
`semconv must be one of latest, legacy, got <v>`). Otherwise read
`OTEL_SEMCONV_STABILITY_OPT_IN` (comma-separated, trimmed): the token
`gen_ai_latest_experimental` selects `latest`; the default is `latest` too (kept separate
so the default can stay put when a newer generation appears). A token ending `/dup` is
ignored with a one-time warning: old and new names are **never** emitted together.
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` equal to `true` (trimmed,
case-insensitive) turns content capture on; default off.

**Record classification.** span record = `meta.kind == "span"` and string `meta.span_id`;
LLM record = has an `llm` object; tool record = `meta.kind == "action"` and non-empty string
`meta.tool`. Order of precedence in the span exporter: LLM, tool, span; anything else is
ignored.

**Common attributes** (added to every spec): `gen_ai.conversation.id` ← `meta.thread_id`;
`gen_ai.agent.name` ← `meta.agent_name`; `logquill.run_id`, `logquill.node_name`;
`logquill.retry_count` (integer, not bool); `error.type` from `meta.error`: the text before
the first `:` trimmed, or `_OTHER` if empty or containing a space. Empty strings count as
absent.

**LLM span.** Operation `text_completion` if `meta.operation == "text_completion"` else
`chat`. Attributes: operation; provider (`meta.provider` or the default); request model
(`llm.model`); response model (`meta.response_model`); input/output tokens (integers);
finish reasons `[llm.finish_reason]`; `logquill.cost_usd`; when content capture is on,
`input_messages`/`output_messages` as compact JSON text; then the common attributes.
Name `"<operation> <model>"` (or just the operation without a model); kind client;
duration = `llm.latency_ms`; error if there is an `error.type` or level is ERROR/FATAL;
event from state diff.

**Tool span.** Name `"execute_tool <tool>"`, kind internal, attributes operation=
`execute_tool`, tool name, tool call id, common; duration = `meta.duration_ms`; error if
`error.type`.

**Span.** If `meta.operation == "invoke_agent"` or `agent_name` is set: agent =
`agent_name` or the logger name; attributes operation `invoke_agent`, provider, agent
name, agent id; name `"invoke_agent <agent>"`; kind client. Otherwise name = record
message, kind internal, no operation. Duration = `meta.duration_ms`; error if level is
ERROR/FATAL.

**State-diff event.** When `meta.state_diff` is an object: event
`logquill.state_diff` with attribute `logquill.state` = compact JSON of the diff.

**Log attributes** (for the logs transport): only for LLM records — operation `chat`,
request model, tokens, finish reasons, `logquill.cost_usd`.

## `OTLPTransport` (spans)

Options: `endpoint` (OTLP/HTTP traces URL; default the SDK/env default), `headers`,
`service_name="logquill"`, `span_exporter` (injected exporter, for tests), `processor`
`"batch"` (default) or `"simple"` (else `processor must be 'batch' or 'simple', got <v>`),
`semconv_version`, `default_provider="unknown"`, `capture_content` (default from env),
`timeout=10` s. Ignores the formatter.

Per record that classifies as a span: end = the record timestamp; start = end − `int(duration_ms
× 1e6)` ns when the duration is truthy, else end. Span id = `span_id_for(meta.span_id)`
when set (else generated). Trace id = `trace_id_for(meta)`; if the record names a parent
and no trace, `trace_id_from_parent(parent)`. With a parent, the parent context is the
remote span `(trace_id, span_id_for(parent))` flagged sampled; with none, an explicit
root (never the host's active span). Attributes and events from the spec; status ERROR
with description `meta.error` if it is a string. Resource `service.name`. Timestamps are
exact integers (P's float conversion loses sub-microsecond precision; do not copy).
`flush()` exports pending spans; `close()` flushes then shuts down.

## `OTelLogsTransport` (logs)

Same constructor shape (`log_exporter`, URL is the OTLP/HTTP logs endpoint). Every record
becomes one OTLP log record: body = message; severity text = level name; severity number
TRACE 1, DEBUG 5, INFO 9, WARN 13, ERROR 17, FATAL 21; timestamp = observed timestamp =
record time. Attributes in this order: each `meta` entry (null skipped; key `stack` renamed
`exception.stacktrace`) via the value rule below; `logquill.logger`;
`logquill.schema_version`; then the log attributes (these override). Trace and span id:
owner span = `meta.span_id` if set, else `meta.parent_span_id`; trace = `trace_id_for(meta)`
else `trace_id_from_parent(parent)`. A log record carries ids only when both are known
(`run_id`/`trace_id` stay attributes otherwise).

**Attribute value rule.** Strings, booleans and finite numbers pass through. A non-empty
list whose items are all the same scalar type (string, bool, or number) passes through as
a list. Anything else (object, mixed list, null inside a list, non-finite number) becomes
compact JSON text (fallback: the value's string form).

## MCP trace propagation

Keys: `logquill/traceparent` and `logquill/run-id` inside the request's `_meta`.
- `propagate(trace_id=None, run_id=None)` → `{ "logquill/traceparent":
  "00-<trace_id or fresh 32 hex>-<fresh 16 hex>-01", "logquill/run-id": run_id (if given) }`.
- `inbound(meta, server, tool)` around handling one request: set the context traceparent
  to `meta["logquill/traceparent"]` if it is a string (restored after); bind context
  `mcp = {server, tool, run_id?}` where `run_id` comes from `meta["logquill/run-id"]` when
  a non-empty string. The client's run id is informational and never replaces the server's.

## ECS mapping (Elasticsearch)

Copy the record, then add: `@timestamp` = timestamp; `log.level` = level lower-cased;
`service.name` = logger; `trace.id` ← `meta.trace_id` and `span.id` ← `meta.span_id` when
present; when `meta.stack` is a string, `error.message` = the record's message and
`error.stack_trace` = the stack. LogQuill's own fields stay alongside. The Kibana export is
`docs/kibana/logquill-dashboard.ndjson` (index pattern `logquill-*`, a saved search, two
visualisations, one dashboard).
