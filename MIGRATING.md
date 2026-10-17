# Migrating from logquill 1.x to 2.0

2.0 changes the license, raises the minimum Python version, and extends the
record shape shared with `logquill` on npm. Application code that only calls
`logger.info(...)` and friends through the top-level `from logquill import
...` API needs no changes; what changes is the license, which Python you run,
what's on disk and on the wire, and — if you import a submodule directly
rather than through the top-level package — where some of those submodules
now live.

## 1. License change: MIT → FSL-1.1-MIT

**This only affects new releases.** 1.0.0 and every earlier version already
on PyPI stay MIT forever — relicensing later code doesn't and can't change
the terms of what's already published. If you're pinned to 1.x, nothing
here affects you.

Starting with 2.0.0, `logquill` is source-available under
[FSL-1.1-MIT](LICENSE) (the Functional Source License, MIT Future variant)
instead of MIT. In short: free for internal use, non-commercial education
and research, and professional services you provide to a licensee; a
commercial product or service that competes with `logquill` itself needs a
separate commercial license from the author. Each version converts to plain
MIT two years after that version's own release. See the
[License](README.md#license) section of the README for the full
explanation, and talk to your own legal/compliance process before upgrading
if license terms matter for how your organization consumes dependencies —
this is a real, binding change for anyone building on 2.0+, not a formality.

## 2. Python 3.10 or newer

`logquill` 2.0 requires Python 3.10+ (it was 3.8+). It is tested on 3.10
through 3.14, including free-threaded (PEP 703) 3.13t/3.14t builds — see
[Thread safety](README.md#thread-safety). On an older interpreter, pip keeps
installing the 1.x line.

## 3. Every record carries `schema_version`

Records now start with `"schema_version": "2.0"`:

```json
{"schema_version":"2.0","timestamp":"2026-01-01T00:00:00.000Z","level":"INFO","logger":"app","message":"hello","meta":{}}
```

**If you read logs** — a parser, a dashboard, a SIEM rule — make sure it
tolerates one extra top-level key. Nothing was renamed or removed; a strict
"exactly these five keys" check is the only thing that breaks.

**If you have existing 1.x logs**, they don't need converting. A record with no
`schema_version` is a 1.x record, and `parse_record` reads both:

```python
import json

from logquill import parse_record

line = '{"timestamp":"2026-01-01T00:00:00.000Z","level":"INFO","logger":"app","message":"hi"}'

record = parse_record(json.loads(line))  # a 1.x or 2.x line
assert record["schema_version"] == "1.0"  # "2.0" for a 2.x record
assert record["meta"] == {}
```

It fills in `"1.0"` and an empty `meta` if either is missing, keeps every other
key, and raises `ValueError` — saying what to fix — for a record that isn't a
LogQuill record, or one written by a newer major version.

**Hash-chained logs** (`TamperEvidentPlugin`) keep verifying: a chain written by
1.x verifies unchanged, and 2.x chains additionally cover `schema_version` and
`llm`, so editing either is caught.

## 4. New optional fields

These are defined so both languages agree on them. `logger.llm_call()` writes
the `llm` block, `span(capture_state=...)` writes `state_diff`, and repeated tool
calls get `retry_count` automatically; nothing else writes them, and no record
has them unless you use those.

| Field | Where | Type |
|---|---|---|
| `llm.model`, `llm.finish_reason` | top level, `llm` block | string |
| `llm.tokens_in`, `llm.tokens_out` | `llm` block | integer ≥ 0 |
| `llm.cost_usd`, `llm.latency_ms` | `llm` block | number ≥ 0 |
| `meta.retry_count` | `meta` | integer ≥ 0 |
| `meta.state_diff` | `meta` | object |
| `meta.mcp.server`, `meta.mcp.tool`, `meta.mcp.run_id` | `meta` | string |
| `meta.tool`, `meta.tool_call_id`, `meta.provider`, `meta.agent_name`, `meta.agent_id`, `meta.response_model` | `meta` | string |
| `meta.operation` | `meta` | `chat`, `text_completion` or `invoke_agent` |
| `meta.input_messages`, `meta.output_messages` | `meta` | array |
| `meta.system_instructions` | `meta` | array |
| `meta.tool_arguments`, `meta.tool_result` | `meta` | any JSON value |

`llm` is its own block, not part of `meta`, because cost and latency
dashboards need stable names for it. A record that isn't an LLM call has no
`llm` key.

## 5. The published schema

`schema/record.schema.json` (JSON Schema 2020-12) defines a record precisely.
The top level is closed — `schema_version`, `timestamp`, `level`, `logger`,
`message`, `meta` and `llm` only — so put your own fields in `meta`, which stays
free-form. `schema/golden_records.json` holds example records that this package
and `logquill` on npm both test against.

## 6. Small things

- `LogRecord` gained `schema_version` (required) and `llm` (optional), and
  `create_record()` sets them. If you build `LogRecord` values by hand in your
  own transport or plugin, add `schema_version=SCHEMA_VERSION`, or build them
  from `create_record()`.
- If a transport of yours rebuilds a record from its five 1.x fields, copy the
  record instead (`{**record, "meta": new_meta}`) so `schema_version` and `llm`
  aren't dropped.

## 7. Content capture is off by default

The fields above that can carry a prompt, a completion, or a tool's
arguments/result — `input_messages`, `output_messages`,
`system_instructions`, `tool_arguments`, `tool_result`, and `state_diff` —
are now governed by a per-logger content-capture policy, `Logger(
content_policy="off" | "hash" | "truncate" | "full")`, **off by default**.
If you were relying on these fields reaching a transport as given, add
`content_policy="full"` explicitly. `AuditLogger` (new in this release) also
defaults to `"off"`.

`RedactPlugin` also gained `classes=` (e.g. `RedactPlugin(classes=
["secret", "content"])`), redacting by field classification instead of only
by exact key name — see `logquill.privacy.FIELD_CLASSES`. Existing
`RedactPlugin()` calls with no arguments are unaffected: the default key set
is unchanged, just now sourced from that same classification.

## 8. Internal modules moved into themed subpackages

**This only affects code that imports a submodule directly** — e.g. `from
logquill.mcp import inbound` or `import logquill.logger`. Every name
documented as part of the public API and reachable via `from logquill
import ...` (`Logger`, `RunPlugin`, `MetricsPlugin`, `Neo4jTransport`, and
so on — see the README) is unaffected; this section only matters if you
bypassed that and reached into a specific file.

Twenty-two top-level modules that had grown into more than one
implementation each were grouped into five subpackages:

| Old path | New path |
|---|---|
| `logquill.logger`, `.levels`, `.records`, `.context`, `.exceptions`, `.handler`, `.opt` | `logquill.core.*` |
| `logquill.worker`, `.shutdown`, `.toggle`, `.runtime_level`, `.serverless` | `logquill.runtime.*` |
| `logquill.span`, `.retry`, `.mcp`, `.semconv` | `logquill.tracing.*` |
| `logquill.cli`, `.webui`, `.trace_tree` (+ the `static/` assets) | `logquill.cli.*` |
| `logquill.privacy`, `.audit` | `logquill.privacy.*` |

`logquill.config`, `logquill.parsing`, and the `logquill.formatter`
compatibility shim stayed exactly where they were — each is a single
implementation with no sibling to group it with. The packaged `logquill`
CLI command is unaffected either way (its `console_scripts` entry point
changed internally, from `logquill.cli:main` to `logquill.cli.cli:main`,
which is invisible to anyone running the installed `logquill` command).

## 9. What else is new

Everything below is additive — none of it requires a change to existing
code, only an explicit opt-in if you want it. See the README section
linked for a runnable example of each:

- **OTel GenAI alignment & `OTLPTransport`** — spans named and attributed
  per the OpenTelemetry GenAI semantic conventions; `OTelLogsTransport` for
  the plain logs bridge. [LLM calls & OpenTelemetry](README.md#llm-calls--opentelemetry)
- **Auto-instrumentation & MCP** — `logquill.instrument.anthropic/.openai/.litellm`
  patch a provider SDK with no call-site changes; MCP trace-context
  propagation helpers. [Auto-instrumentation and MCP](README.md#auto-instrumentation-and-mcp)
- **Local-first trace viewer** — `logquill trace`, `logquill serve`, and
  `logquill dev`, all offline, no account, no backend.
  [CLI](README.md#cli)
- **Privacy, content policy & audit trail** — field classification,
  `logquill verify <file>`, and the `AuditLogger` preset.
  [`AuditLogger`: a preset for audit trails](README.md#auditlogger-a-preset-for-audit-trails)
- **Runtime intelligence** — `FlightRecorderPlugin`, `RunSummaryPlugin`,
  `AdaptiveSamplingPlugin`, and changing a logger's level without a
  restart. [Runtime level control](README.md#runtime-level-control)
- **Metrics bridge & graph transports** — `MetricsPlugin` (OTel/StatsD/
  Prometheus Pushgateway backends), and `Neo4jTransport`/`FalkorDBTransport`/
  `NeptuneTransport` writing a run's span tree straight into a graph
  database. [Metrics bridge](README.md#metrics-bridge),
  [Graph transports](README.md#graph-transports)
- **Performance & free-threaded Python** — `JSONFormatter(backend="orjson")`
  (`pip install logquill[fast]`), and every batching transport/`FileTransport`/
  `HTTPTransport` now locks around its shared buffer, tested under PEP 703
  free-threaded 3.13t/3.14t in CI. [Thread safety](README.md#thread-safety)
