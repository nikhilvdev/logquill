# 09 — Parity: Python is the baseline, every binding closes the gap

## Rule

1. **Baseline = Python 2.0.** Every binding (Node, WASM, JVM, Swift, Go, .NET, Ruby)
   implements the full Python feature set. If a feature cannot exist in a language, the
   binding says so in its docs and offers the nearest equivalent; it is never silently
   absent.
2. **Adopt JS where it is better practice.** Anything JS does better is adopted by *all*
   bindings, including Python. List below.
3. **Fix what both get wrong.** The audit defects (decisions D20–D27) are fixed once, in the
   core, for everyone.
4. Most of rule 1 costs nothing per language: the features that matter live in the Rust
   core, so JS gains them by construction. Only the host-specific layer (adapters, driver
   transports, bridges) is per language.

## A. Features JS lacks today → every binding gets them

| Feature (Python today) | Where it lives in the new code | JS gets it via |
|---|---|---|
| Adaptive sampling, flight recorder, run summary | core | core |
| `MetricsPlugin` + StatsD / Prometheus / OTel metrics backends | core + runtime | core |
| `AuditLogger` preset, signed chain head, detailed/signed verification | core | core |
| `RedactPlugin(classes=…)`, 12 PII/secret patterns, `redact_local` | core | core |
| `diagnose` (locals in tracebacks, redacted first) | SDK per language | Node SDK: capture via error cause/stack plus a bounded, redacted `locals` hook where the runtime allows it; document the limit |
| `AppriseAlertPlugin` (100+ services) | optional per language | Node: generic webhook + the same plugin interface; use an Apprise-compatible service if one exists (verify at Stage 6) |
| Graph transports (Neo4j, FalkorDB, Neptune) | core rows + SDK driver | Node SDK drivers |
| CLI (`tail`, `trace`, `dev`, `serve`, `verify`) | Rust binary | same binary |
| Adapters: CrewAI, AutoGen, LlamaIndex, OpenAI Agents | SDK | only where the framework has a JS SDK (check LlamaIndex.TS, OpenAI Agents JS at Stage 6); CrewAI and AutoGen are Python-only frameworks |
| LiteLLM patch | Python SDK only | n/a (Python library) |
| Config: `class:` reference, YAML, formatter by name | core parse + SDK | Node SDK |
| HTTP bounds: byte limit and timeout | runtime | runtime |
| SQL table-name validation | core | core |
| `flush(timeout) → bool`, exit hook, `disable/enable` | core + SDK | Node SDK |
| LLM-call, MCP, state-diff, retry fields | core | core |

## B. Things JS does better → adopted everywhere (including Python)

| JS practice | Adopt as |
|---|---|
| Release job runs lint, typecheck and tests before publishing (D30) | Every registry's publish job depends on the full test matrix of the tagged commit |
| Actions pinned to commit SHAs, least-privilege permissions, concurrency control, a fixed required job name | All workflows |
| Package hygiene gate (`check-pack`: allowlist of files, exports present, secret-looking files, size limits) | One gate per artifact type: wheel/sdist contents, npm tarball, crate package list, Maven/NuGet/gem contents |
| Supply-chain workflows: CodeQL, dependency review, OpenSSF Scorecard, security audit, changelog check | Added to the monorepo |
| Daily drift check between repos | Replaced by one corpus tested in every binding in one repo, plus a daily check that the *published* releases still pass it |
| `NO_COLOR` support in the console transport (D9) | Core console transport |
| Elasticsearch API-key auth, and a warning when a formatter is ignored (D11) | Runtime Elasticsearch sink; same warning pattern for other sinks that ignore the formatter |
| Syslog TCP backpressure cap of 1 MiB (D17) | Runtime syslog sink |
| SQS chunks sent in parallel (D15) | Bounded parallelism in every SQS sink |
| Namespaced Redis stream default `logquill:logs` (D13) | All |
| Legacy camelCase meta rename in `parse_record` (D18) | Core |
| Browser/edge build with the documented non-isolation trade-off | The WASM binding; the same caveat stated in every browser-like host |
| `OtelSpanProcessor` (turn live OTel spans into records) | Python SDK gains an OpenTelemetry `SpanProcessor` too; other bindings follow where the host has an OTel SDK |
| pino and winston bridges | Python gains `structlog` and `loguru` bridges; JVM gains SLF4J/Logback; .NET gains `Microsoft.Extensions.Logging`; Go gains `slog`; Swift gains `os.Logger`/swift-log; Ruby gains `Logger` |
| `Logger({ meta })` base-meta option | Python gains `Logger(meta=…)`; all bindings accept it |
| SDK instrumentation by **client instance** (isolated, handles dual ESM/CJS copies) | Python gains `instrument.openai(logger, client=…)` and `anthropic(…, client=…)` as an alternative to the global patch; both modes in every binding that patches SDKs |
| `serialized.id` last segment as the fallback span name in the LangChain adapter | Python adapter gets the same fallback |
| `installShutdownHandlers` (flush and exit on `SIGTERM`/`SIGINT`) | Python gains an opt-in `install_signal_handlers(logger)`; every binding offers one; container stop no longer loses the queue |
| Explicit DynamoDB 25-item chunking and its own marshalling | Kept in the SDKs that talk to DynamoDB |
| Timers that do not keep the process alive (`unref`) | Alert-window timers and watchers never block exit |

## C. Where the right answer is neither language's current behaviour

| Topic | Decision |
|---|---|
| Default dispatch (D2) | Non-blocking by default in every language, because the contract says a log call never waits on I/O. Python's synchronous default becomes opt-in (`sync=True`) and is called out in the migration guide. JS already behaves this way. |
| `child()` plugins (D1) | Not inherited by default (Python's safer behaviour); privacy plugins and the tamper chain are inheritable so a child can never be less protected than its parent. JS users get this as a fix for the shared-`RunPlugin` counter. |
| Content-off placeholder (D3) | One neutral string, fixed in a fixture. |
| `block` backpressure (spec 05) | Keep Python's true blocking (the producer waits); JS's "run inline" becomes the same wait-for-space behaviour, so the policies mean the same thing everywhere. |
| Sampling/flight-recorder flush (D34) | Elevated records go through the normal dispatch path, not straight to sinks. |

## D. Acceptance test for parity

A binding is "at parity" when:
1. Every shared fixture in `schema/corpus/` passes in it.
2. Every behaviour in `01`–`07` that is not marked host-only has a test in its suite,
   traceable to a line in `legacy-tests/python-tests.txt` (and, where JS had one, in
   `js-tests.txt`).
3. Its public API exposes every method named in `01` under its idiomatic name, and the
   mapping table (`docs/api-names.md`, written in Stage 0) lists each name for each
   language.
4. Features marked "n/a" in section A are listed in its README with the reason.
