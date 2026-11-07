# The cross-language contract

Every LogQuill binding agrees on the following. A change to any of it is a **breaking change for
all packages** and means a major version (see [Versioning and stability](versioning.md)).
The machine-readable definition is [`schema/record.schema.json`](../schema/record.schema.json); the
fixtures every language runs are in [`schema/`](../schema/README.md).

## Record

```json
{
  "schema_version": "2.0",
  "timestamp": "2026-11-08T10:15:30.123Z",
  "level": "INFO",
  "logger": "checkout",
  "message": "order placed",
  "meta": { "order_id": 42 },
  "llm": { "model": "claude-sonnet-5-5", "tokens_in": 812, "tokens_out": 140 }
}
```

- `schema_version` is on every record. A record without it is read as `"1.0"`.
- `llm` appears only on LLM-call records. There are no other top-level keys.
- `meta` is always an object. Named `meta` keys are `snake_case` in every language.

## Levels

`TRACE(5) DEBUG(10) INFO(20) WARN(30) ERROR(40) FATAL(50)`: same names and weights everywhere.

## The `llm` block

All fields optional, no others allowed: `model` (string), `tokens_in` / `tokens_out` (integer ≥ 0),
`cost_usd` / `latency_ms` (number ≥ 0), `finish_reason` (string).

## Agent and trace fields (in `meta`)

| Field | Meaning |
|---|---|
| `kind` | `thought`, `action`, `observation` or `decision` (free-form otherwise) |
| `run_id` | Groups one agent run |
| `span_id`, `parent_span_id` | Per-step ids; a span id is 16 lowercase hex characters |
| `duration_ms` | Stamped when a span closes |
| `trace_id` | Follows one request across services (W3C `traceparent`, X-Ray or GCP header, or generated) |
| `thread_id`, `node_name` | LangGraph: a resumable conversation across runs, and the node that produced the record |
| `retry_count` | Integer ≥ 0; how often the same tool call was reopened |
| `state_diff` | Object; state before and after a span |
| `mcp.server`, `mcp.tool`, `mcp.run_id` | Model Context Protocol correlation |
| `tool`, `tool_call_id`, `provider`, `agent_name`, `agent_id`, `response_model`, `operation` | GenAI hints; `operation` is `chat`, `text_completion` or `invoke_agent` |

## Privacy

- **Content policy.** `input_messages`, `output_messages`, `system_instructions`, `tool_arguments`,
  `tool_result` and `state_diff` are *content* fields. A per-logger policy governs them:
  `off` (default), `hash`, `truncate` or `full`. It is applied before any plugin or transport sees the record.
- **Field classes.** `secret`, `pii` or `content`. A redaction rule can target a class instead of listing keys.
  `password`, `token`, `secret`, `api_key` and `authorization` are always redacted.

## Plugins and transports

- Plugin hooks: `before_log(record) → record | null` (null drops it), `after_log(record)`, `on_error(error, record)`.
- Transport contract: `format(record) → string`, `write(formatted, record)`, non-blocking for the caller,
  `close()` flushes.

## OpenTelemetry

Span ids, trace ids and the `gen_ai.*` attribute names follow fixed rules, pinned to one
semantic-conventions version and tested by `schema/golden_otel_ids.json`. Prompt and completion text is exported
only when content capture is on; the content policy still applies first.

## Public names

[API names](api-names.md) lists every public name per language.
