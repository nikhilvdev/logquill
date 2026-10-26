# Shared corpus

Fixtures that every binding runs. Each `<group>.json` holds cases of the shape

```json
{
  "name": "hash policy on an object",
  "input": { "policy": "hash", "json": "{\"b\":1,\"a\":[1.0,\"é\"]}" },
  "expected": { "value": "sha256:…" },
  "differs": { "js": { "value": "sha256:…" } },
  "rule": "D4 — a float keeps its .0 in the canonical text"
}
```

A binding reads only `input` and `expected`: feed the input to its implementation and
compare the result with `expected`. `differs` and `rule` are an audit trail, not something
to test: they record what the Python 2.0.0 or JavaScript reference printed when it did not
match `expected`, and which decision in `docs/spec/00-decisions.md` settled it. The
file-level `js_gaps` lists features the JavaScript package lacks; every binding still has to
reach them (`docs/spec/09-parity-and-gap-closing.md`).

Numbers are compared the way a JSON reader sees them: `1` and `1.0` are different, `true`
is not `1`. A binding that cannot keep that difference (JavaScript) must take its inputs as
JSON text and say so in its test harness.

## Groups

| Group | What it checks | Stage |
|---|---|---|
| `canonical-json` | canonical JSON text and its SHA-256 for hostile values | 1 |
| `hash-chain` | chaining, signing, and what verification reports for edited, removed, reordered and truncated logs | 1 |
| `content-policy` | `off` / `hash` / `truncate` / `full`, and the invalid-policy error | 1 |
| `field-classes` | the registry and `keys_in_class` | 1 |
| `levels` | parsing a level from a name or number, and its errors | 1 |
| `records` | timestamps, the `llm` block builder and its warnings, reading records back | 1 |
| `trace-headers` | trace ids from W3C, X-Ray and GCP headers | 2 |
| `otel-ids` | OpenTelemetry span and trace ids derived from LogQuill ids | 2 |
| `redaction` | key redaction and the twelve PII patterns, nested and hostile values | 2 |
| `formatters` | JSON, text and logfmt lines, and reading logfmt back | 2 |
| `ecs` | the Elastic Common Schema document | 2 |
| `semconv` | GenAI span and log attributes, convention selection, timestamps | 2 |
| `sql-rows` | the SQL row and table definition per dialect | 2 |
| `graph-rows` | graph node, parent edge, session and the shared statement | 2 |
| `trace-tree` | rebuilding and rendering a run's span tree | 2 |
| `sampling`, `rate-limit`, `adaptive-sampling`, `flight-recorder`, `run-summary`, `run-plugin`, `alert-dedupe` | stateful plugins stepped through an injected clock and random draw | 2 |

The scenario groups (the last row) describe a plugin by `options` and a list of `steps`;
each step may set the clock (`clock`, seconds) and the next random draw (`rng`) before it
presents one record. Both persist until changed. `expected` lists, per step, whether the
record was kept, what the plugin wrote straight to its transports, and the plugin's state at
the end. A binding builds the plugin with the same options, an injectable clock and random
source, and a transport that records writes.

Also in `schema/`: `record.schema.json` and `golden_records.json` (the record shape),
`golden_chain.json` (a signed chain) and `golden_otel_ids.json`, each older than the corpus and
still run by every binding.

## Regenerating

`scripts/corpus/` holds the generator. `inputs/<group>.json` are hand-written, including
the hostile cases; `ref_py.py` and `ref_js.mjs` run the references on them; `build.py` applies
the settled decisions and writes this directory.

```bash
python3 scripts/corpus/build.py            # rewrite every group
python3 scripts/corpus/build.py sampling   # one group
python3 scripts/corpus/build.py --check    # fail if a committed group is out of date
python3 scripts/corpus/build.py --report   # the divergence report (docs/spec/10-corpus-divergences.md)
```

The references are pinned in `scripts/corpus/references.json` (Python tag `v2.0.0`, and one
commit of `logquill-js`). By default the generator unpacks the tag with `git archive` and
looks for `../logquill-js` with `npm ci` done; set `LOGQUILL_PY_DIR` or `LOGQUILL_JS_DIR` to
use other copies. A build stops with `UNSETTLED` if a reference disagrees with the expected
output and no decision explains it.

The generator needs the old releases, so it is a Stage 0 tool: once the 2.x and 1.x lines are
retired, the committed files are the contract and are changed by hand, with a decision,
or by a generator that is rewritten against the Rust core.
