# LogQuill on a Rust core — architecture, inventory and publishing plan

Status: proposal, written on the `develop` branch. Nothing here is built yet.

Basis: a read of all of `logquill-python` (110 modules, ~11.4k lines, ~13k lines of
tests, `schema/`, workflows) and of `logquill-js` (84 modules, ~8.7k lines, 1.0.1
published, 2.0 unreleased on a branch). Every feature found is placed in section 4.

## 1. Decision

One monorepo whose `develop` line contains **no Python or TypeScript library
source of its own**. It holds:

- a Rust core and runtime that own everything that must behave identically in every
  language, and
- one `bindings/<language>/` directory per target, each holding that language's
  native glue **and its idiomatic SDK layer** (the part that has to be written in the
  host language: context propagation, framework adapters, SDK patching, host-driver
  transports).

So the current Python and JS code is not copied into a `packages/` folder. It is
replaced: shared logic moves into Rust, host-specific logic is rewritten inside the
matching binding. The two existing repos keep their `main` branches as the 2.x
(Python) and 1.x (JS) maintenance lines; `develop` becomes the 3.0 line.

Consequence to accept up front: there is **no pure-Python or pure-TypeScript
fallback** on `develop`. Python gets native wheels only; Node gets native packages
with an automatic WebAssembly fallback; "zero required dependencies" becomes "no
Python or npm dependencies" (a prebuilt binary is still a dependency on a platform
matrix). Section 7 lists what this costs.

### The old code is documented, not kept

Because the old sources will not be in the tree, everything needed to rebuild them is
written down here and next to this file:

- `docs/spec/` — a behaviour specification of both codebases (≈ 1,600 lines): exact
  defaults, algorithms, regular expressions, message texts, wire formats, and every place
  Python and JavaScript differ (`docs/spec/00-decisions.md`, 34 numbered decisions with a
  recommendation each). Start at `docs/spec/README.md`.
- `docs/spec/legacy-tests/` — one line per existing test (775 Python, 525 JS): the checklist
  each ported SDK must cover.
- `docs/legacy/` — verbatim copies of both READMEs, changelogs and migration guides (the
  documented examples and public contract), the viewer page `trace_viewer.html`, and the
  two benchmark sources.
- `schema/` — the record schema and golden fixtures, which stay.
- Git history: the `v2.0.0` tag in `logquill-python` and `v1.0.1` in `logquill-js` are the
  reference implementations; Stage 0 pins them as test dependencies.

## 2. What the read showed

These findings shape the design.

1. **The contract is enforced only in part.** `schema/` covers record shape, one hash
   chain and the OTel id rules. Redaction, PII patterns, content policy, logfmt/text
   output, sampling, rate limiting, trace-header parsing and the trace tree exist
   twice with no shared fixtures. With the old code gone from the tree, the fixtures
   must be generated from the old implementations first (Stage 0), or there is
   nothing to hold the port to.
2. **The tamper chain hashes Python's JSON, not JSON.** `_compute_hash` uses
   `json.dumps(content, sort_keys=True, default=str)`: `", "` / `": "` separators,
   `ensure_ascii=True`, Python float `repr`, `str()` for non-JSON values, keys sorted
   by code point. The JS repo carries `canonical-json.ts` and a pure-JS `sha256.ts`
   to imitate this, and documents one known difference (keys sort by UTF-16 unit, not
   code point, around astral characters). One Rust implementation removes the
   imitation. Whether JS 1.x chains verify under this rule was not checked.
3. **Records are mutable dicts.** Plugins mutate the same dict. Across an FFI
   boundary that is a copy, so built-in plugins run **inside** Rust with no crossing;
   only a host-language plugin (`logger.use(fn)`) is a callback. A logger with no host
   plugins never crosses on the hot path.
4. **`meta` is arbitrary host objects** (circular references, `default=str`, objects
   whose `__str__` raises) and the adversarial property tests assert logging never
   raises. Conversion to a JSON value happens in the binding with a depth cap, cycle
   guard and string fallback, never in the core.
5. **Host-only by nature:** context variables, current-span propagation, `opt(depth)`
   stack inspection, `lazy` callables, `exc_info`/`diagnose` tracebacks, the stdlib
   `logging.Handler` bridge, serverless wrappers, exit hooks, signals, SDK patching,
   every framework adapter and every driver-based transport. They live in the
   bindings.
6. **Three plugins write straight to transports** (`SamplingPlugin` elevation,
   `FlightRecorderPlugin`, `RunSummaryPlugin`), bypassing later plugins. In the core
   this is an explicit "side channel to sinks", documented, not an accident.
7. **Regex semantics differ.** The PII patterns use `\b` and `\d`; `\d` is Unicode in
   Python `str` and Rust `regex`, ASCII in JavaScript. The core fixes one semantics
   (Rust `regex`; the current patterns need no lookaround). A user-supplied
   `re.Pattern` cannot be handed to the core: the SDK accepts a pattern string or
   keeps that plugin on the host side.
8. **Four "cloud" transports are plain HTTP** (Datadog, New Relic with gzip and
   `Retry-After` backoff, Elasticsearch `_bulk` with the ECS mapper, Application
   Insights). They move to the Rust runtime and stop being written twice. CloudWatch,
   Cloud Logging, DynamoDB, SQS, Pub/Sub, Kafka, RabbitMQ, Mongo, Redis, Postgres,
   MySQL and the graph databases wrap host drivers and stay host-side.
9. **Small mismatches between the repos.** The original spec lists gzip and zip
   compression on rotated files. The JS `FileTransport` has `gz` only; the Python one
   has none and only renames. The Rust file transport implements both. The JS
   `VERSION` constant says `0.3.0` while `package.json` says `1.0.1`; the port
   single-sources the version from the workspace. The config `class` key is a dotted
   path in Python and `"module#Export"` in JS; each SDK keeps its own form.
10. **Python is far ahead of JS, and that is the rule, not an accident.** Python 2.0 is the
    baseline every binding must reach; where JS is the better practice it is adopted by
    all, Python included; defects in either are fixed once in the core. JS lacks adaptive
    sampling, flight recorder, run summary, metrics, graph transports, Apprise, the CLI and
    viewer, `diagnose`, `AuditLogger`, and four adapters — most of these arrive in JS for free
    once the logic is in the core. Python in turn gains what only JS had: the browser/edge
    build, `OtelSpanProcessor`, a base-`meta` option, per-client SDK patching, a signal
    helper that flushes on `SIGTERM`, and the `structlog` / `loguru` bridges that mirror
    pino / winston. The full lists, and the parity acceptance test, are in
    `docs/spec/09-parity-and-gap-closing.md`.
11. **Defects found in the audit are design requirements, not things to port:**
    a failing redaction plugin must drop the record (fail closed); the PII scan must
    cover non-JSON leaf values by their string form; the hash chain must be computed
    where records are written, one at a time; the stdlib bridge must ignore
    LogQuill's own diagnostics; the worker must survive `fork`; the "already
    elevated" and "already flushed" id sets must be bounded; a failed file rotation
    must reopen the file; `RedactPlugin` must recurse into nested values.
12. **Licensing differs by repo.** Python 2.0.0 is FSL-1.1-MIT, JS 1.0.1 is MIT, and
    Python 1.x and earlier are MIT permanently. One decision is needed before code
    moves (section 8).

## 3. Target layout

This is the destination. Today `develop` contains only `crates/logquill-core`,
`crates/logquill-cli`, `schema/` and `docs/`; everything else below is added in the stage
that needs it.

```
logquill/                          (monorepo; develop = the 3.0 line)
├── crates/
│   ├── logquill-core/             pure Rust: no I/O, no threads, injected clock and RNG
│   ├── logquill-runtime/          worker, queue, batching, native transports, watchers
│   ├── logquill-ffi/              stable C ABI + generated header (cbindgen)
│   └── logquill-cli/              tail / trace / dev / serve / verify + embedded viewer
├── bindings/
│   ├── python/                    PyO3 + maturin, mixed layout: Rust ext + `logquill/` SDK
│   ├── node/                      napi-rs + TypeScript SDK (+ wasm fallback)
│   ├── wasm/                      wasm-bindgen + browser / Deno / Workers SDK
│   ├── jvm/                       UniFFI Kotlin + Kotlin/Java SDK
│   ├── swift/                     UniFFI Swift + Swift SDK
│   ├── go/                        wazero + core.wasm + Go SDK
│   ├── dotnet/                    P/Invoke over logquill-ffi + C# SDK
│   └── ruby/                      `ffi` gem over logquill-ffi + Ruby SDK
├── schema/                        record schema + shared corpora (generated in Stage 0)
├── docs/                          architecture, migration guides, Kibana dashboard
└── .github/workflows/
```

**`logquill-core`** — levels; records and `parse_record`; canonical JSON and the
tamper chain (verify, head signing with HMAC-SHA256); field classes and content
policy; redaction and PII logic; JSON / text / logfmt formatters and `parse_logfmt`;
trace-header parsing (W3C, X-Ray, GCP); OTel id derivation and the `semconv` mapping
(one file, pinned to one convention version); ECS mapper; SQL row mapping, DDL text
and identifier validation; graph row mapping and the shared Cypher statement; the
rate-limit, sampling, adaptive-sampling, flight-recorder, run-summary, retry and
alert-dedupe state machines; the trace-tree builder and run summaries; the
count-and-byte batching buffer; the bounded queue with `drop_oldest`, `drop_newest`
and `block`; the `Logger` pipeline itself (level check, enable/disable rules,
content policy, plugin order, side channel to sinks).

**`logquill-runtime`** — worker thread; console; rotating file (size rotation,
gzip/zip compression, Fernet encryption); HTTP NDJSON; the four HTTP cloud sinks;
syslog (RFC 5424, UDP/TCP); OTLP/HTTP traces and logs (JSON encoding, so no protobuf
dependency); SQLite sink (also used by the viewer); Slack, PagerDuty and email
senders; StatsD and Prometheus-pushgateway backends; level file/env watchers. A host
transport registers with the core as a callback sink.

**FFI shape.** Coarse, so crossings are rare: `Logger::new(config) -> handle`,
`log(level, logger, message, meta, llm?) -> Option<record>`, `child`, `set_level`,
`flush`, `close`, plus pure helpers (`verify_chain`, `redact_text`, `parse_record`,
`trace_tree`, `canonical_json`). Python and Node convert `meta` natively; the other
bindings pass UTF-8 JSON.

## 4. Inventory: where every existing feature goes

`P` = in the Python repo today, `J` = in the JS repo, `PJ` = both. "SDK" means the
binding's host-language layer; "port" means rewritten there from the specification in
`docs/spec/` (the old code will be gone). Each row below maps to a spec file: records,
logger, spans and context → `01`; plugins → `02`; privacy, hashing, tracing, OTel → `03`;
formatters, parsers, config → `04`; runtime and every transport → `05`; CLI and viewer →
`06`; adapters, patching, bridges, browser → `07`; tests, budgets, CI → `08`.

### 4.1 Records, levels and the Logger

| Feature | Today | Home | Notes |
|---|---|---|---|
| Levels `TRACE…FATAL`, `parse_level` | PJ | core | |
| Record shape, `schema_version`, `create_record`, millisecond UTC timestamp, `parse_record` (1.x records read as `"1.0"`) | PJ | core | |
| `llm` block builder with per-field validation | PJ | core | bad field dropped with a diagnostic, never raises |
| `trace…fatal`, `thought/action/observation/decision`, `llm_call` | PJ | core + SDK | SDK keeps idiomatic signatures (keyword args / options object) |
| `child`, `use`, `set_level`, `flush`, `flush_async`, `close` | PJ | core handle + SDK | child shares sinks; plugins are per logger |
| Function-as-middleware (`FunctionPlugin`) | PJ | SDK | host callback wrapped as a plugin |
| `Plugin` hooks `before_log` / `after_log` / `on_error` / `redact_local` | PJ | core trait + SDK subclass | |
| Retry tracking (`meta.retry_count`) | PJ | core | bounded |
| `disable()` / `enable()` / `is_enabled` | PJ | core | copy-on-write rules |
| `opt(lazy, depth)` / `OptLogger`, `caller` info | PJ | SDK | stack frames and callables are host objects |
| `exc_info`, `diagnose`, traceback formatting (JS: an `Error` in `meta.err` becomes `meta.stack`) | P, J (no `diagnose`) | SDK + core `redact_text` | locals redacted before formatting |
| `bind_context` / `current_context` | PJ | SDK | ContextVar / AsyncLocalStorage / browser stack |
| `span()` / `SpanContext`, `capture_state`, `state_diff` | PJ | SDK scope + core ids, diff and record builder | span id 16 hex |
| Content policy applied before any plugin | PJ | core | |
| `AuditLogger` preset (+ `sign_head`, shared chain in children) | P | core preset | JS gains it |

### 4.2 Runtime, config, formatting

| Feature | Today | Home | Notes |
|---|---|---|---|
| `AsyncWorker` / `DispatchQueue`, three backpressure policies, once-a-minute drop warning | PJ | runtime | must survive `fork` |
| Exit flush hook, shutdown handlers | PJ | SDK + core `close` | `atexit` / process signals |
| `with_lambda`, `with_cloud_function`, `with_azure_function`, `withFlush` | PJ | SDK | wrap handler, flush in `finally` |
| Runtime level: signal handler | PJ | SDK | OS signals are host-specific |
| Runtime level: file and env watchers | PJ | runtime | |
| Config: `load_config`, from file / env, JSON and YAML, built-in `type` registry, `formatter` by name | PJ | core parse + SDK | |
| Config `class:` import (`a.b.C` in Python, `module#Export` in JS) | PJ | SDK | trust boundary unchanged |
| JSON, text, logfmt formatters; `format_text`; `parse_logfmt`; `TEXT_LOG_PATTERN` | PJ | core | |
| `parse(path, regex, cast)` | PJ | SDK | uses the host regex on purpose (legacy log formats) |
| `orjson` fast path | P | dropped | native serialization replaces it |
| `canonical-json.ts`, pure-JS `sha256.ts` | J | dropped | core does both |

### 4.3 Plugins

| Plugin | Today | Home | Notes |
|---|---|---|---|
| `ContextPlugin`, `RunPlugin`, `RateLimitPlugin` | PJ | core | |
| `RedactPlugin` | PJ | core | recurse into nested values; fail closed |
| `PIIRedactPlugin` (email, SSN, card, phone, API-key shapes, JWT, private-key block, bearer) | PJ | core | one regex semantics; non-JSON leaves scanned as text |
| Presidio mode | P | Python SDK, optional | ML dependency stays host-side |
| `SamplingPlugin` incl. tail elevation | PJ | core | bounded elevated set |
| `AdaptiveSamplingPlugin`, `FlightRecorderPlugin`, `RunSummaryPlugin` | P | core | JS gains them |
| `TraceContextPlugin` (header parsing, id generation) | PJ | core | |
| …active OTel span lookup | PJ | SDK | each language's OTel API |
| `TamperEvidentPlugin`, `verify_chain_detailed`, `sign_head`, `verify_signed_chain` | PJ | core | chained at write time under one lock |
| `AlertingPlugin` (threshold, dedupe window, bounded keys, never blocks) | PJ | core state machine + runtime | one thread pool, not a thread per alert |
| Slack, PagerDuty, Email senders | PJ | runtime | |
| `AppriseAlertPlugin` | P | Python SDK, optional | Apprise is a Python library |
| `MetricsPlugin` | P | core | JS gains it |
| StatsD, Prometheus-pushgateway backends | P | runtime | |
| OTel metrics backend | P | runtime (OTLP) or SDK bridge | |
| `OtelSpanProcessor` (OTel spans → records) | J | Node SDK | Python gains an equivalent |

### 4.4 Privacy, tracing and OpenTelemetry

| Feature | Today | Home | Notes |
|---|---|---|---|
| `FIELD_CLASSES`, `CONTENT_FIELDS`, content policy `off/hash/truncate/full` | PJ | core | |
| `semconv` (pinned 1.44.0, `latest` / `legacy`, opt-in env, never both) | PJ | core | |
| OTel id rules (`_ids`) | PJ | core | `golden_otel_ids.json` |
| `OTLPTransport` (spans), `OTelLogsTransport` (logs) | PJ | runtime | native OTLP/HTTP by default |
| …injecting a host OTel exporter / SDK pipeline | PJ | SDK bridge, optional | tests use in-memory exporters |
| MCP `propagate` / `inbound` (`logquill/traceparent`, `logquill/run-id`) | PJ | core helpers + SDK scope | |
| ECS mapper | PJ | core | |

### 4.5 Transports

| Transport | Today | Home | Notes |
|---|---|---|---|
| Console (stderr for ERROR/FATAL, colour) | PJ | runtime | browser: SDK |
| File (rotation, Fernet encryption; `gz` compression in JS only) | PJ | runtime | add `gz` and `zip` everywhere; keep `fernet_decrypt`, `generate_key` helpers |
| HTTP NDJSON (`urllib` / `aiohttp` backends) | PJ | runtime | `aiohttp` backend dropped |
| Datadog, New Relic, Elasticsearch (+ECS), App Insights | PJ | runtime | |
| Syslog | PJ | runtime | |
| SQLite | PJ | runtime | rusqlite; also read by the viewer |
| Postgres, MySQL | PJ | core row mapping + SDK driver | identifier validation in core |
| MongoDB, Redis streams, DynamoDB | PJ | SDK | partition-key rule in core |
| Kafka, RabbitMQ, SQS (10-message cap), Pub/Sub | PJ | SDK | key and batch rules in core |
| CloudWatch, Cloud Logging | PJ | SDK | |
| Graph: `BaseGraphTransport`, Neo4j, FalkorDB, Neptune | P | core row + Cypher, SDK driver | Kuzu stays skipped |
| `BeaconTransport` | J | wasm SDK | |
| `BatchingTransport` base | PJ | core buffer | host sinks get a batch callback |

### 4.6 CLI and viewer

| Feature | Today | Home | Notes |
|---|---|---|---|
| `tail` (level, `-n`, `-f`, `--json`) | P | cli | stream; handle rotation; bounded for `-n` |
| `trace`, `dev` | P | cli | streaming tree builder |
| `serve` + `trace_viewer.html` (396 lines) | P | cli | HTML embedded in the binary |
| `verify` (+ signed head) | P | cli | |

### 4.7 Framework adapters, instrumentation, bridges

| Feature | Today | Home |
|---|---|---|
| Adapter base class | PJ | each SDK |
| LangChain, LangGraph | PJ | Python SDK, Node SDK |
| CrewAI, AutoGen (via stdlib logging), LlamaIndex, OpenAI Agents | P | Python SDK |
| Anthropic, OpenAI patching (non-streaming) | PJ | Python SDK, Node SDK |
| LiteLLM patching | P | Python SDK |
| MCP instrumentation | PJ | Python SDK, Node SDK |
| stdlib `logging.Handler` bridge | P | Python SDK |
| pino destination, winston transport, level-dispatch helper | J | Node SDK |
| Browser entry: stack-based span, browser context, no Node built-ins | J | wasm SDK |

New adapters (Pydantic AI, Claude Agent SDK) stay "scoped, not built".

### 4.8 Tests, benchmarks, CI, docs

| Asset | Today | Home |
|---|---|---|
| Python tests (~60 files) and hypothesis suites | P | specification for the ported SDK tests; property tests re-expressed with `proptest` in the core |
| JS tests (vitest, fast-check, browser no-built-ins test) | J | same |
| Memory-budget benchmarks (`measure.py`, `bench/memory.bench.mjs`) | PJ | criterion + an allocation budget in CI |
| `schema/` records, chain, OTel ids | PJ | `schema/` + generated corpora |
| JS `scripts/ci` (pack check, consumer smoke, OTel compat) | J | per-binding install smoke tests |
| JS workflows: CodeQL, scorecard, dependency review, security, changelog | J | kept, pointed at the monorepo |
| pdoc / typedoc sites | PJ | rustdoc + per-binding docs |
| `docs/kibana`, `MIGRATING.md`, changelogs | PJ | `docs/`, one changelog |

## 5. Language tiers

| Tier | Target | Binding | Registry | Notes |
|---|---|---|---|---|
| Easy | Python ≥ 3.10 | PyO3 + maturin | PyPI `logquill` | `abi3` wheels for 3.10–3.14; free-threaded 3.13t / 3.14t need separate wheels |
| Easy | Node ≥ 20, TypeScript | napi-rs | npm `logquill` + `@logquill/native-<platform>` | automatic WASM fallback |
| Easy | Browser, Deno, Bun, Workers | wasm-bindgen | npm `@logquill/wasm` | core only; SDK supplies transports |
| Medium | JVM, Android | UniFFI Kotlin | Maven Central | native libraries per OS |
| Medium | Swift | UniFFI Swift | SwiftPM binary target | XCFramework + checksum |
| Medium | Go | wazero + `core.wasm` | Go module proxy | no cgo; measure call cost first |
| Medium | C# / .NET | P/Invoke over `logquill-ffi` | NuGet | RID-specific native assets |
| Medium | Ruby | `ffi` gem over `logquill-ffi` | RubyGems | precompiled platform gems |
| Later | PHP, Elixir, … | `logquill-ffi` | — | on demand |

Why the C ABI for Go / .NET / Ruby: UniFFI's own generators cover Kotlin, Swift,
Python and Ruby. Go and C# come from a third party (NordSecurity) whose current
maintenance I could not confirm, and a thin wrapper over a stable C ABI costs less
than depending on a generator we do not control.

**What each SDK layer must contain** (the Python and Node ones are the largest):
facade with idiomatic call shapes; context and span scope; host-plugin bridge;
`opt`/`lazy`/caller and exception formatting where the language has them; stdlib or
ecosystem logging bridge; serverless and exit hooks; config class resolution;
driver-based transports (AWS, GCP, SQL, queues, Mongo, Redis, graph); framework
adapters and SDK patching; OTel bridge. Medium-tier SDKs ship only a facade, context
scope, console/file/HTTP and host-plugin callbacks, then grow on demand.

## 6. Stages

Each stage ends with its exit criteria met and a merge to `develop`; only the last
stage tags 3.0.0. The first two stages are broken down into ordered work packages in
`docs/stages/stage-0.md` and `docs/stages/stage-1.md`. Tag the old `main` branches (`py-2.x`, `js-1.x`) before Stage 0.

**Stage 0 — Freeze and extract the contract** (no Rust)
- Monorepo skeleton: **done on `develop`, kept deliberately small** — a Cargo workspace with
  `logquill-core` and `logquill-cli`, Rust CI, and the Python library, tests, benchmarks and
  Python tooling removed (they stay on `main` and under the `v2.0.0` tag). The runtime and
  C ABI crates and the eight binding folders are added when their stage starts; a tarball of
  the first scaffolding of them (manifests, minimal sources, a version-sync script, a
  Makefile) was set aside outside the repository and can be re-created from `docs/`. Still to do: tag and
  keep the old lines for maintenance, and settle the repository name before the first
  publish. A pending 2.x change set (OTel log span ids, shared golden fixtures, SQL
  table-name check) was set aside in `git stash` and belongs on a 2.x branch off `main`.
- Resolve the open decisions in section 8 and settle the items in
  `docs/spec/00-decisions.md` (D1–D45; each has a recommendation; change it there).
- Turn `docs/spec/` into executable fixtures: **done** — 22 groups in `schema/corpus/`,
  generated by `scripts/corpus/` from the two reference releases, with every divergence
  listed in `docs/spec/10-corpus-divergences.md`. Check each ported behaviour against
  `docs/spec/legacy-tests/` as its stage lands.
- `docs/api-names.md`: every public method and option under its idiomatic name in each
  language, so "at parity" (spec 09, section D) can be checked mechanically. **Drafted**;
  review it and fill in the options and arguments as each binding's API is designed.
- Build `schema/corpus/` by running **both** existing implementations on one shared
  input set and recording outputs: redaction, PII, content policy, logfmt, text, JSON,
  trace-header parsing, semconv span specs, id derivation, sampling / rate-limit /
  adaptive / flight-recorder / alert-dedupe (seeded RNG, fake clock), run summary,
  trace tree, tamper chains (including non-ASCII, astral keys, `-0.0`, integers over
  2^53, NaN / Infinity), SQL and graph row mapping, ECS documents.
- Every place the two disagree is listed and settled by a written rule.
- Exit: corpus checked in; a CI job installs the last published `logquill==2.0.0`
  and `logquill@1.0.1` from their registries and re-checks the corpus, so the
  reference implementations stay a pinned test dependency, not tree source.

**Stage 1 — Core: records and integrity**
- Levels, records, `parse_record`, canonical JSON, tamper chain, head signing, field
  classes, content policy, retry tracker, enable/disable rules.
- Exit: `golden_records`, `golden_chain` and the new chain corpus pass; logs signed
  by Python 2.0.0 verify in Rust and the reverse; `cargo-fuzz` on `parse_record` and
  the canonicaliser; `proptest` mirrors the old property suites.

**Stage 2 — Core: processing and pipeline** — done, see `docs/stages/stage-2.md`
- Redaction, PII, formatters, parsers, trace headers, semconv, ids, ECS, SQL and
  graph row mapping, all state machines, trace-tree builder, the `Logger` pipeline.
- Exit: every corpus passes in Rust; a raising redactor drops the record; the chain
  is verifiable under 8 concurrent writers.

**Stage 3 — Runtime** — done, see `docs/stages/stage-3.md`
- Worker, queue, batching, console / file (rotation, compression, encryption) / HTTP
  family / syslog / SQLite / OTLP, senders, metrics backends, watchers.
- Exit: the burst-and-stall test (tens of thousands of records, stalled sink) keeps
  memory set by the queue bound; fork test passes; benchmark and allocation budgets
  enforced in CI; Miri / loom (or equivalent) on the queue.

**Stage 4 — CLI and viewer** — done, see `docs/stages/stage-4.md`
- `tail`, `trace`, `dev`, `serve`, `verify` with the page embedded.
- Exit: a ~1 GB file is traced and tailed with bounded memory; output matches the old
  CLI on the fixture files; `tail -f` follows across rotation.

**Stage 5 — Python binding and SDK** — started, see `docs/stages/stage-5.md`
- Extension module, then the SDK layer from section 5, then adapters, patching,
  host transports, bridges.
- Also adds what Python gains from JS (spec 09, section B): `OtelSpanProcessor` equivalent,
  `Logger(meta=…)`, per-client SDK patching, `install_signal_handlers`, `structlog` and
  `loguru` bridges, and the LangChain fallback span name.
- Exit: the ported Python test suite passes (the old suite is the checklist); the
  adversarial `meta` tests pass; per-call latency and allocations are at least as good
  as 2.0.0; free-threaded CI green; install-and-run smoke test from a built wheel.

**Stage 6 — Node binding and SDK**
- napi-rs module with WASM fallback; SDK layer; pino / winston bridges; LangChain and
  LangGraph adapters; patching; `OtelSpanProcessor`; host transports.
- Also closes the gap to Python (spec 09, section A): every Python plugin, the CLI, the
  graph and metrics sinks, `AuditLogger`, `diagnose`, config `class:`/YAML, and the
  adapters for frameworks that have a JS SDK.
- Exit: ported JS suite passes on Node LTS lines; corpus passes; pack and consumer
  smoke tests pass for ESM and CJS; the parity acceptance test (spec 09, section D) passes.

**Stage 7 — WASM and browser**
- wasm-bindgen module, browser SDK (beacon, console, stack-based span).
- Exit: corpus passes in headless browser, Deno and Workers; the "no Node built-ins"
  test is kept.

**Stage 8 — Medium tier, in expected order of demand**
- `logquill-ffi`, then Go, JVM, Swift, .NET, Ruby.
- Exit per language: corpus green on every OS the platform supports; install from the
  registry into a clean project works; the README example is run in CI.

**Stage 9 — Docs and 3.0.0**
- Migration guides (Python 2.x → 3.0, JS 1.x → 3.0), docs regenerated, one changelog,
  contract-table version bumped everywhere together, release checklist below.

## 7. Publishing

The step-by-step guide for every registry (setup, build, publish, verify, undo) is
`docs/publishing.md`; this section is the overview.

**Versioning.** One lockstep version for the crates and every binding, because the
contract version is the compatibility signal. The first release from `develop` is
3.0.0 in every language.

**One tag, many registries.** Pushing `vX.Y.Z` runs: check that the tag equals every
manifest → build matrix → tests on the exact tagged commit → per-registry publish
jobs. Nothing is published from a laptop. Each registry's first release goes through
a GitHub environment with required reviewers. Third-party actions are pinned to
commit SHAs.

| Registry | Artifact | Auth | Provenance |
|---|---|---|---|
| crates.io | `logquill-core`, `logquill-runtime`, `logquill-ffi`, `logquill-cli` | trusted publishing (OIDC) | — |
| PyPI | `logquill` wheels (manylinux, musllinux, macOS x86_64 / arm64, Windows) + sdist | trusted publishing via `PyO3/maturin-action` | attestations |
| npm | `logquill`, `@logquill/native-<platform>`, `@logquill/wasm` | trusted publishing (npm CLI ≥ 11.5.1) | provenance |
| Maven Central | `logquill` JAR / AAR with native libraries | Central Portal token + GPG key (secrets) | signatures |
| SwiftPM | tag + `LogQuill.xcframework.zip` on the release, checksum in `Package.swift` | none | — |
| Go | module tag (sub-module tags if the module is not at the repo root) | none | checksum DB |
| NuGet | `LogQuill` with `runtimes/<rid>/native` | API key secret until OIDC is confirmed | — |
| RubyGems | platform gems + source gem | trusted publishing | — |

CLI distribution: `cargo install logquill-cli`, release binaries per platform, and the
Python wheel and npm package carry the binary so `logquill tail` keeps working after
`pip install logquill` (bundling a binary inside a maturin wheel should be confirmed
in Stage 5).

**New repo or rename.** PyPI and npm trusted publishers are bound to a repository,
workflow file and environment name. Publishing the 3.0 line from a differently named
repository means configuring each trusted publisher again. Decide the repository name
in Stage 0, before the first publish.

Rules carried over from today: CI green on the exact tagged commit; the tag is the
only trigger; no skipped versions — fix forward on the same version; check every
registry page after the run; update the status notes last.

**Matrix per release:** Linux x86_64 / aarch64 (glibc and musl), macOS x86_64 /
arm64, Windows x86_64 / arm64; Python 3.10–3.14 plus 3.13t / 3.14t; Node LTS lines.
A platform with no prebuilt binary builds from source (needs a Rust toolchain) or, on
Node, uses the WASM fallback. Python has no such fallback — say so in the install docs.

**Name claims** (checked 2026-10-26; HTTP 404 means free): crates.io `logquill` and
`logquill-core`; RubyGems `logquill`; NuGet `logquill`; npm `@logquill/core`. npm
`logquill` and PyPI `logquill` are already ours. Maven group ID and the Go module path
still need choosing and verifying. Claim the free names with a placeholder release
before Stage 5.

## 8. Risks and open decisions

1. **Licence.** The core under FSL-1.1-MIT (current Python) or MIT (JS and all 1.x)?
   Relicensing the JS code is the author's call, and the licence text has had no legal
   review.
2. **JS 2.0.** It is unreleased on a branch. Finish and ship it on the old line, or
   skip to 3.0 and avoid a third contract generation? Recommendation: skip unless it
   is nearly done.
3. **Hash canonicalisation drift.** Mitigated by the chain corpus and cross-version
   verification; a mismatch blocks release.
4. **FFI cost.** Host-plugin callbacks and `meta` conversion can erase the gain on
   small records. The Stage 5 gate is a measured benchmark; plugin-heavy loggers may
   stay slower and that has to be documented, not hidden.
5. **No pure fallback.** A Python user on an unsupported platform cannot install.
   Mitigation is the wheel matrix and an sdist build; consider Pyodide wheels later.
6. **Rewrite volume.** The Python SDK layer alone is roughly adapters (~1.3k lines),
   patching (~0.4k), host transports (~2k), bridges and facade. Treat the old code as
   the spec and the old tests as the checklist; do not translate line by line.
7. **Support surface.** Eight bindings and a platform matrix is a large commitment for
   one maintainer. Stages 8+ are ordered so any language can be stopped without
   affecting the rest.
8. **Unverified tooling facts.** Free-threaded wheels needing non-abi3 builds, the
   wasm-bindgen / wasm-pack ownership change after the rustwasm org was sunset, NuGet
   OIDC support, and bundling a binary in a maturin wheel should be re-checked against
   current documentation when each stage starts.
9. **Not checked in the old code.** Read in full: every Python module except the test
   files' bodies, and the JS core, plugins, config, runtime-level, OTel, bridges, adapters,
   instrumentation and the cloud, file, console, HTTP, SQL-base, SQS and queue-base
   transports. Read only partly (defaults and signatures): the JS Mongo, Redis, Kafka,
   RabbitMQ, Pub/Sub, DynamoDB, SQLite, Postgres and MySQL transports, and the JS
   `sha256.ts`, `load.ts` and `OtelSpanProcessor` internals beyond the processor itself.
   Test bodies in both repos were catalogued by name only. JS 1.x tamper-chain
   serialisation was not verified. Anything that turns out missing from `docs/spec/` should
   be recovered from the `v2.0.0` / `v1.0.1` tags and added there.
