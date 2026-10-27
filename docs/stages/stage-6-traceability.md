# Stage 6 — traceability of the legacy tests (parity condition 2)

Spec 09 section D asks that every behaviour in `docs/spec/01`–`07` that is not host-only has a test in the
binding's suite, traceable to a line in `docs/spec/legacy-tests/python-tests.txt` (and `js-tests.txt` where JS had
one). This is what is established for the Node binding, and what is not.

## The JavaScript checklist: mechanical

`js-tests.txt` lists 525 tests. Each was matched by file and title against the tests that ran in `sdk/test/`:
**524 are present**. The one that is not is `pii-redact-plugin.test.ts`'s circular-reference case, which was
rewritten on purpose: it expected a record whose `meta` contains itself to be written unredacted, and decision D20
says a redaction plugin that cannot do its job drops the record. The new test is
`a circular reference withholds the record instead of writing it unredacted, and does not crash`.

## The shared corpus: mechanical

All 22 groups of `schema/corpus/` run against the addon (`tests/corpus.test.mjs`, 913 tests including the
slice-1 helpers), on Node 22, 24 and 26 and on the WebAssembly build. The SDK's own plugins run the `redaction`,
`sampling`, `rate-limit`, `run-plugin` and `hash-chain` groups (`sdk/test/parity/`), with the addon and without it.
Two `hash-chain` cases are skipped in the SDK because a JavaScript number cannot tell `1.0` from `1`.

## The Python checklist: by area, not by line

`python-tests.txt` lists 775 tests in about 100 files. A title cannot be matched across languages, so this maps
each area to where Node covers it. It is a judgement made from the test layout, not a line-by-line trace.

| Python area (tests) | Node |
|---|---|
| plugins: sampling, rate limit, run, tamper evident, redact, PII (about 70) | corpus groups, plus the original JS tests |
| adaptive sampling, flight recorder, run summary (42) | corpus groups; `sdk/test/native/features.test.ts` for the plugin wrappers |
| privacy, field classes, content policy (20) | corpus `content-policy`, `field-classes`, `redaction` |
| levels, records, timestamps, contract, golden ids (about 30) | corpus `levels`, `records`, `otel-ids`; `contract.test.ts` |
| semconv, OTLP, OTel logs (52) | corpus `semconv`; the original JS `test/otel/` |
| formatters, parsing (about 35) | corpus `formatters`; the original JS tests |
| trace tree (12) | corpus `trace-tree` (the viewer itself is the Rust CLI) |
| config, context, opt, lazy, exceptions, retry, state diff, toggle, runtime level, serverless, shutdown (about 120) | the original JS tests for each |
| cloud, SQL, NoSQL, queue, batching, console, file, HTTP transports (about 80) | the original JS tests for each; the file transport also runs on Rust |
| alerting: Slack, PagerDuty, email, base (17) | the original JS tests |
| instrumentation: Anthropic, OpenAI; MCP (25) | the original JS tests (`logquill/instrument`) |
| adapters: LangChain, LangGraph, base (12); LlamaIndex, OpenAI Agents (11) | the original JS tests; `openai-agents-adapter.test.ts` and `llamaindex-adapter.test.ts` |
| audit (13), metrics and backends (28), graph transports (20) | `features.test.ts` and `parity-extra.test.ts` (about 30 tests): every Python case with a Node counterpart is ported; the OTel metrics backend has its own file; the Python `OTelMetricsBackend` default-resolution case maps to the global-meter test |
| worker, backpressure, async logger (24) | the SDK's queue tests, plus the Rust worker's own tests in `crates/logquill-runtime`; no Node burst test |

### Not applicable to Node

CLI and web UI (about 60 tests): the Rust `logquill` binary, shared by every language. `diagnose` (10): JavaScript cannot
read another frame's locals. Apprise (9): a Python library. CrewAI, AutoGen, LiteLLM (17): Python-only frameworks.
The stdlib `logging` handler (5): Node's equivalents are the pino and winston bridges, which are covered. Version,
lazy-import and aiohttp tests (about 17): Python packaging details.

### Gaps: Python has tests, Node has no equivalent code

- Native alert senders and level watchers: now exposed to Node (`RustSlackAlertPlugin`, `RustPagerDutyAlertPlugin`,
  `RustEmailAlertPlugin`, `RustLevelWatcher`), tested in `sdk/test/native/alerts.test.ts`; the JavaScript plugins remain
  for the no-addon path.
- The line-by-line trace of the Python tests is `stage-6-python-trace.md` (generated, heuristic; see its header).
- LlamaIndex and OpenAI Agents adapters now exist (`sdk/test/adapters/`), tested against objects shaped from the frameworks'
  published type declarations; they have not been run against the installed libraries.
- `structlog` and `loguru` bridges: Python-only libraries, so n/a, not gaps.
