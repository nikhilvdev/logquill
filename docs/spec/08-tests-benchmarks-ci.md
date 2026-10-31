# 08 — Tests, benchmarks, CI and release

## Test inventory

The complete test-name lists are in `legacy-tests/python-tests.txt` (775 functions) and
`legacy-tests/js-tests.txt` (525 cases). Counts below are test functions; parametrised
tests multiply. Each ported SDK must cover the same behaviours; the core must cover the
shared ones through fixtures.

Python, by area: adapters 35 (base 1, LangChain 5, LangGraph 6, CrewAI 6, AutoGen 6,
LlamaIndex 6, OpenAI Agents 5) · plugins 190 (tamper-evident 21, adaptive sampling 18, PII
16, run summary 13, metrics 12, flight recorder 11, trace context 11, sampling 10, plugin
base 10, prometheus 9, Apprise 9, alerting 7, rate limit 7, redact 7, StatsD 7, run 5, OTel
metrics 5, PagerDuty 4, email 3, Slack 3, context 2) · transports 107 (cloud 26, graph 20,
queue 10, SQL 9, NoSQL 7, file 8, HTTP 8, batching 7, aiohttp 6, console 3, base 3) ·
OpenTelemetry 35 (OTLP 19, logs 11, golden ids 3, wire 2) + semconv 20 · CLI/viewer 60 (web
UI 20, verify 10, `tail` 9, `dev` 9, `trace` 8, `serve` 4) · privacy 20, audit 13, contract
15, config 19, opt 17, tracing 15 + 2 end-to-end, runtime level 15, worker 13, retry 12,
trace tree 12, diagnose 10, MCP 10, state diff 9, lazy imports 9, parsing 9, exceptions 8,
instrument 20, logger async 7, LLM call 7, shutdown 7, toggle 7, serverless 6, context 6,
logger 6, levels 5, timestamp 5, handler 5, backpressure burst 4, version 2 · property
tests 13 (pipeline 3, formatters 5, transports 5).

JS, by area (files under `test/`): adapters (LangChain, LangGraph), bridges (pino, winston),
browser (context, span, no-Node-built-ins), core (context, dispatch queue, formatter(s),
levels, logger API + pipeline + two property suites, privacy, serverless, shutdown, tracing),
instrument, otel (semconv), plugins (13 files incl. OTel span processor), transports
(beacon, console, file, HTTP, cloud, NoSQL, OTel, queue, SQL), plus config, contract, index
and runtime-level tests.

## Shared fixtures that exist today (`schema/`)

`record.schema.json` (JSON Schema 2020-12) and `golden_records.json` (27 valid, 39 invalid,
5 legacy, 11 rejected cases: valid records must validate and round-trip; invalid must not
validate; legacy must parse to exactly `parsed`; rejected must be refused by the parser),
`golden_chain.json` (4 chained records), `golden_otel_ids.json` (22 cases). JS CI checks its
copies against this repository's `main` daily.

## Property-test generators

Python (hypothesis) for hostile `meta`: scalars = none, booleans, arbitrary integers,
floats **including NaN and ±Infinity**, text, binary, a raw `object()`; values = those
nested in lists (≤ 5 items) and dicts (keys 1–10 characters, ≤ 5 entries), ≤ 25 leaves;
`meta` dicts have ≤ 8 keys of 1–10 characters. Text for formatters also includes lone
surrogates (no category filtering), ≤ 40 characters. Circular references are tested
separately. Properties: logging with any `meta` never raises; formatters never raise;
transports survive any record; the pipeline fails closed (drops or logs an internal
warning). The property files are skipped where the library is unavailable (free-threaded
3.13). Rust equivalent: `proptest` with the same generators plus `cargo-fuzz` on the parser
and canonicaliser.

## Memory budgets (CI gates)

Python (`measure.py`, copied in `../legacy/python/`): scenario `meta` =
`{user_id: 42, route: "/checkout", duration_ms: 12.5, ok: true, tag: "a"×40}`; sink = a
transport that discards; "plugin" logger = `ContextPlugin(service="api")` + `RedactPlugin` +
`PIIRedactPlugin`. Tracemalloc medians over 100 samples after 50 warm-ups; retained blocks
measured over 20 000 calls with the cyclic GC off after 200 warm-ups.

| Metric | Budget |
|---|---|
| retained objects per call | 0.05 |
| peak bytes, call filtered by level | 1 000 |
| peak bytes, filtered `opt(lazy=True)` call | 1 200 |
| peak bytes, plain call | 12 000 |
| peak bytes, call through the plugin stack | 14 000 |
| growth over a 100 000-record burst into a stalled sink, queue 1 000, `drop_oldest` | 3 000 000 |

Plus: a deliberately leaking plugin and a deliberately bloated plugin must make the gate
fail (a gate that cannot fail protects nothing); a metric that was not measured is a
failure; 5× the burst must cost under 2× the memory for both drop policies (queue 500,
bursts 20 000 vs 100 000).

JavaScript (`memory.bench.mjs`, copied): 20 000 warm-up and 100 000 measured calls through
a logger with an ample queue (200 000) and `ContextPlugin` + `RedactPlugin`, forced GC
around the measurement: ≤ **2 000 bytes/call** (baseline ≈ 640). Burst: 50 000 calls into a
queue of 1 000 (`dropOldest`): the queue size must never exceed 1 000 and heap growth must
stay ≤ **50 MiB**.

Rust targets: keep the shapes (bytes and objects per call, bounded burst) and re-derive the
numbers from the first native baseline; add criterion latency tracking for information.

## CI and release as they exist

Python:
- `ci.yml`: on PRs and pushes to `main`. Matrix Python 3.10–3.14: `pip install -e
  ".[dev,http,apprise,otel]"`, `ruff check .`, `mypy logquill`, `pytest --cov`. Separate
  benchmark job (3.12, `pytest benchmarks`, no coverage because line tracing distorts
  allocation counts). Free-threaded job 3.13t / 3.14t: asserts the GIL is off, installs a
  reduced set (some native dependencies do not build on 3.13t), runs the tests and the
  benchmarks.
- `release.yml`: tag `v*` → check the tag equals `pyproject.toml`'s version
  (`Tag <tag> does not match pyproject.toml version <v> — fix the tag or bump the version
  before releasing.`) → `python -m build` → publish with PyPI trusted publishing from the
  `pypi` environment (`id-token: write`, no stored token).
- `docs.yml`: pdoc site to GitHub Pages on `main`.
- Pre-commit: ruff (with fix), ruff-format, mypy strict, trailing whitespace, end-of-file.
- Lint config: ruff line length 100, rules E F W I UP B C4 SIM; mypy `strict`, Python 3.10.
- Process rules (in the project notes): CI green on the exact tagged commit; the tag is the
  only release trigger; never publish by hand; no skipped versions; update the status block
  last.

JavaScript:
- `ci.yml`: `test` job (Node 22: `npm ci`, lint, typecheck, coverage, build, memory
  benchmark) plus a compatibility matrix on other Node lines and macOS. Actions pinned to
  commit SHAs; least-privilege permissions; concurrency control; the job name `test` is
  fixed because branch protection requires it.
- Other workflows: `changelog`, `codeql`, `contract` (daily schema-copy comparison against
  the Python repo's `main`), `dependency-review`, `docs` (typedoc), `package` (pack checks),
  `scorecard`, `security`.
- `scripts/ci/check-pack.mjs`: the tarball may contain only `dist/`, `LICENSE`, `README.md`,
  `package.json`; flags secret-looking files; every `exports` target must be in the tarball;
  limits 1 500 000 bytes packed and 6 000 000 unpacked. A consumer smoke project and an
  OTel-compatibility smoke script exist under `scripts/ci/`.
- `release.yml`: on tag, check, install npm ≥ 11.5.1 (trusted publishing), lint, typecheck,
  test, build, `npm publish --access public` with provenance.

## Packaging facts

Python: build backend hatchling; wheel packages `logquill`; sdist allowlist (source, tests,
benchmarks, schema, top-level docs, licence); `py.typed`; console script `logquill`;
`requires-python >=3.10`; no required dependencies; licence FSL-1.1-MIT from 2.0.0 (1.x and
earlier MIT). Extras: `http`, `apprise`, `otel`, `instrument-anthropic`, `instrument-openai`,
`instrument-litellm`, `openai-agents`, `postgres`, `mysql`, `mongodb`, `redis`, `kafka`,
`rabbitmq`, `pubsub`, `gcp-logging`, `aws`, `neo4j`, `falkordb`, `presidio`, `crypto`,
`langchain`, `langgraph`, `crewai`, `llamaindex`, `autogen`, `yaml`, `fast`, plus `dev`,
`hooks`, `docs`.

JavaScript: ESM + CJS builds with types (tsup), `sideEffects: false`, Node ≥ 18 engines,
exports `.`, `./langchain`, `./winston`, `./instrument`, `./browser` (ESM only), an
`imports` map swapping the context and span modules for the browser build, optional peer
dependencies for every driver and OpenTelemetry package (AWS SDK clients, Google Cloud
clients, `amqplib`, `applicationinsights`, `kafkajs`, `mongodb`, `mysql2`, `nodemailer`,
`pg`, `redis`, `winston-transport`, `yaml`, `@langchain/core`, the `@opentelemetry/*` set),
MIT licence.
