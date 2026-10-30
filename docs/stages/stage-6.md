# Stage 6 — Node binding and SDK

Goal: `npm install logquill` gives the JavaScript API on the Rust core, at parity with Python (spec 09).
Plan: `docs/rust-core-plan.md` (sections 3 and 5, Stage 6). Code: `bindings/node/` (napi-rs addon plus the TypeScript SDK; see its README). Legacy-test traceability: `docs/stages/stage-6-traceability.md`.

## Status: complete (all seven slices; the pieces left are listed under "Not done")

Branch `feat/stage-6-node`, cut from `develop` after Stage 5 (PR #32) merged. Nothing here is committed or
pushed yet.

The sibling repo `logquill-js` (its `package.json` says 1.0.1; the 2.0 line is unreleased) is the specification
and the working baseline, the way the published 2.0.0 wheel was for Stage 5. The old JS tests
(`docs/spec/legacy-tests/js-tests.txt`) are the checklist for the exit criterion.

## Layout decisions

- The addon is **outside the Cargo workspace** (`exclude` in the root `Cargo.toml`) with its own lockfile, for
  the reason the Python binding is: napi's macros expand to `unsafe`, which the workspace forbids, and
  `cargo test --workspace` should not need Node.
- Built with `@napi-rs/cli` 3 on `napi` 3 / `napi-derive` 3 (versions checked 2026-10-27: `napi` 3.15.0,
  `napi-derive` 3.6.13, `napi-build` 2.6.0, `@napi-rs/cli` 3.10.9). The loader and `index.d.ts` are generated.
  Targets: macOS arm64 and x64, Linux gnu (x64, arm64) and musl (x64), Windows x64.
- **Pure helpers take and return JSON text, not JavaScript values.** A JavaScript number cannot tell `1` from
  `1.0`, and the canonical text and the tamper chain depend on that (`schema/corpus/README.md`). The hot path
  (`Logger` calls with a `meta` object) gets native value conversion in slice 2, with `meta` treated as hostile
  as in the Python converter.
- `NaN`, `Infinity` and `-Infinity` words in input text are accepted (decision D19; `JSON.stringify` never
  writes them, Python's `json` does). `canonicalJson` uses the core's own reader; the formatters rewrite those
  words to an overflowing literal outside string literals, so a message that says "NaN" is untouched.
- Number results cross as JS numbers: `recordsChecked` and `brokenAt` are `u32`.

## Slices

| # | Slice | State |
|---|---|---|
| 1 | Addon scaffold; pure helpers (`parseRecord`, `verifyChain`, `canonicalJson`, the three formatters, `parseLogfmt`, `utcTimestamp`); golden records, shared chain, corpus groups `canonical-json` and `formatters` | **done** |
| 2 | Baseline TypeScript SDK in place (the `logquill-js` 2.0 tree, its 78 test files); Rust dispatcher behind the `Logger` (bounded queue, worker, exit-safe drain); native file transport (rotation, gzip, Fernet) behind `FileTransport`; native console / HTTP constructors in the addon; ESM and CJS bundles | **done** |
| 3 | Core logic exposed to Node and checked against the shared corpus: redaction, PII, tamper chain and signed chains, content policy, field classes, sampling with tail elevation, rate limit, adaptive sampling, flight recorder, run summary, run stamp, alert de-duplication, levels, records, trace headers and ids, GenAI attribute mapping, ECS / SQL / graph rows, trace trees (**all 22 corpus groups pass**, `tests/corpus.test.mjs`). SDK plugins on the core: `RedactPlugin` and `PIIRedactPlugin` (fail closed, field classes), `AdaptiveSamplingPlugin`, `FlightRecorderPlugin`, `RunSummaryPlugin`. The SDK's `SamplingPlugin`, `RateLimitPlugin`, `RunPlugin` and `TamperEvidentPlugin` were measured against the corpus: `RunPlugin` and the rate limiter agreed, the sampler had two gaps (a `null` trace id, the unbounded elevated set) which are fixed in JavaScript, and the tamper chain differs only where a float has no fractional part | **done** |
| 4 | Metrics (StatsD, pushgateway, OpenTelemetry) with `MetricsPlugin`; the Neo4j / FalkorDB / Neptune graph transports; **Rust-backed syslog, SQLite, Datadog, New Relic, Elasticsearch, Application Insights and OTLP (traces and logs) sinks** (`Rust…Transport` classes); alert de-duplication measured against the corpus (12 of 12 after a fix) | **done**, except the native alert *senders* and level *watchers* (see "Not done") |
| 5 | Node-only layer: context on `AsyncLocalStorage`, spans, config, pino and winston bridges, serverless and exit hooks, `disable`/`enable` | **done by the baseline** (it already had them); `diagnose` is n/a in JavaScript (see README) |
| 6 | `AuditLogger`, signed chains, `OtelSpanProcessor` (baseline), LangChain / LangGraph adapters and Anthropic / OpenAI patching with MCP (baseline), **`OpenAIAgentsAdapter`** (a tracing processor) and **`LlamaIndexAdapter`** (a callback-manager listener), the OpenTelemetry metrics backend | **done** |
| 7 | Ported JS suite (the baseline's 78 files; 524 of the 525 legacy tests present), parity conditions 1 (corpus), 2 (`stage-6-traceability.md`) and 3 (names), ESM / CJS consumer smoke test, **packaging** (real tarballs installed into a clean project, `exports` map, platform package names `@logquill/native-<platform>`), **WebAssembly fallback**, Node 22 / 24 runs, `node` CI job, README | **done**, except: parity condition 2 for Python is by area, not by line; no registry publish (the package stays `private`); Node 20 unsupported; benchmark against the JS package covers only the file path; CI has not run on GitHub |

## Approach (as in Stage 5)

The `logquill-js` working tree (2.0, unreleased) was copied in as `bindings/node/sdk/` with its 78 test files, and
the core pieces are swapped for Rust-backed ones one at a time, each checked by that suite. The oracle is that
tree: the same tests must pass with the native path on and with `LOGQUILL_NO_NATIVE=1` (the pure-JS fallback).
Only three tests needed edits, to find `schema/` at its monorepo location.

## Decisions made in slice 2

- **Records cross as JSON text, not as converted objects.** `JSON.stringify` is much faster than walking a JS
  object across N-API, and hostile `meta` is already handled on the JavaScript side. Native `meta` conversion
  (the plan's slice 2 item) is therefore not built.
- **Transports written in JavaScript stay on the SDK's own queue.** The Rust worker cannot call back into JS
  without the event loop, which a synchronous drain in an `exit` handler cannot give it. Only transports with a
  `nativeHandle` go to the Rust dispatcher; a logger with both kinds writes them in no fixed order.
- **A logger whose transports are all native skips the JS queue.** That queue costs about 49 µs a record to
  drain; with it out of the way, 100k records take about 0.24 s end to end instead of about 4.1 s. The price is
  caller-side latency: about 1.7 µs a call instead of 0.5 µs, because serialising and handing over happen inline
  instead of being deferred. `afterLog` fires once the record is handed to Rust, not once it is written.
- **Console and HTTP stay JavaScript in the SDK.** They write through `console` and `fetch`, which the SDK's tests
  and users replace with fakes, and Rust writing to stdout from another thread could reorder output against the
  application's own `console.log`. The native constructors exist in the addon for hosts that want them.
- **Exit hook.** A native dispatcher is drained synchronously on `process` `exit` (at most 5 s); 20,000 of 20,000
  records reached disk both when a script just ended and under `process.exit(3)`. A hard kill is still the
  caller's job (`installShutdownHandlers`).
- **Conditional import `#native`** (like `#span`, `#context`): Node gets the loader, the browser build gets a stub
  that reports no addon.

## Exit criteria (from the plan)

- The ported JS suite passes on the Node LTS lines.
- The shared corpus passes.
- Pack and consumer smoke tests pass for ESM and CJS.
- The parity acceptance test (spec 09 section D) passes.

## Slice 1 results

- 267 tests in `bindings/node/tests/helpers.test.mjs` pass on Node 26: every golden valid, legacy and rejected
  record, the shared hash chain with edit, removal and reorder cases, and the `canonical-json` and `formatters`
  corpus cases.
- `cargo fmt --check` and `cargo clippy -D warnings` are clean for the addon; the main workspace is unchanged.
- The test harness (`tests/lossless.mjs`) reads fixtures keeping each number's source text; that needs
  `JSON.parse` source-text access (Node 21 and later), so those tests skip on Node 20.

## Slice 2 results

- SDK suite: 648 tests pass on the native path and 645 (+3 skipped native-only) with the addon off. The new
  `sdk/test/native/native-path.test.ts` checks that the Rust file transport is the one in use, that a custom
  formatter falls back to JS, and that a record `JSON.stringify` rejects does not stop later ones.
- Rust-written encrypted and gzipped files are read back by the JS `fernetDecrypt` and gunzip in the existing
  file-transport tests, so the two implementations agree.
- `cargo fmt --check` and `cargo clippy -D warnings` (library target) are clean; `tsc --noEmit` is clean.
- A `node` job in `.github/workflows/ci.yml` builds the addon, runs both test suites twice (native and fallback)
  and lints. It has since run on GitHub and is green (PR #33, run 40585402308; `develop`, run 40586980453) on Ubuntu
  and macOS, with the Node 22 and 24 runs, the WebAssembly build and the pack smoke test added later in this stage.

## Results since slice 2

- **Corpus.** `tests/corpus.test.mjs` runs all 22 groups of `schema/corpus/` against the addon, mirroring the Rust
  reference harness case for case: 913 tests with the slice-1 helpers, identical on Node 22, 24 and 26.
- **A gap the corpus found in the SDK.** The baseline's JavaScript `RedactPlugin` and `PIIRedactPlugin` agreed with the
  corpus on only 48 of 74 redaction cases (no nested-key redaction, no field classes, fewer secret patterns). They now
  use the Rust redactors when the addon loads: 74 of 74 (`sdk/test/parity/corpus-redaction.test.ts`). The JavaScript
  fallback agrees on 57 of 74 (it has no field classes, a shorter pattern list and a slightly different depth rule), so
  **without the addon these two privacy controls are weaker**; `classes` throws rather than quietly doing less.
- **Decision D20 in JavaScript.** `Plugin.failsClosed`: if a redaction plugin throws, `Logger` drops the record instead of
  writing it unredacted. A `meta` that contains itself now withholds the record under `PIIRedactPlugin` (the old test
  expected it to be written unredacted, and was changed).
- **New in the SDK from the core:** `AdaptiveSamplingPlugin`, `FlightRecorderPlugin`, `RunSummaryPlugin`,
  `MetricsPlugin` with `StatsDBackend` and `PrometheusPushgatewayBackend`, `AuditLogger`, `signHead`, `verifyChain`,
  `verifySignedChain` (from raw log lines, so number spellings survive), and the `Neo4jTransport`, `FalkorDBTransport`
  and `NeptuneTransport` graph transports. Tests use a real UDP socket for StatsD and a real HTTP server, in another
  process, for the pushgateway.
- **Native alert senders and level watchers (done).** `alerts.rs` and `watchers.rs` expose Rust Slack / PagerDuty / email
  senders and a file-or-env `LevelWatcher`; `sdk/src/plugins/native-plugins`' sibling `native-alerts.ts` wraps them as
  `RustSlackAlertPlugin`, `RustPagerDutyAlertPlugin`, `RustEmailAlertPlugin` and `RustLevelWatcher`. The JavaScript
  alert plugins and watchers stay for the no-addon and WebAssembly paths. 10 tests (`sdk/test/native/alerts.test.ts`):
  local HTTP receiver, fake SMTP, dedupe, unreachable destination, bad threshold, watcher by file and by env.
- **Totals.** SDK suite: 736 tests on the native path, 648 (+88 native-only skipped) with `LOGQUILL_NO_NATIVE=1`; it
  also passes on Node 22 and 24. `tsc`, `rustfmt` and `clippy -D warnings` (library) are clean. `npm run smoke` loads
  the ESM and CJS bundles as a consumer and checks 500 records on disk (Node 22, 24, 26). Parity condition 3
  (`docs/api-names.md`, Node column) is a test; condition 1 (the corpus) passes.

## Results since the first pass

- **WebAssembly fallback.** The addon has a `runtime` Cargo feature (default on). Without it (`--no-default-features`)
  the crate is the core logic only and builds for `wasm32-wasip1-threads`: `npm run build:wasm` (needs
  `rustup target add wasm32-wasip1-threads` and the emnapi 2.0 prerelease; emnapi 1.11 does not link). The generated
  loader prefers a native binary and falls back to `logquill.wasm32-wasi.wasm`. All 913 addon tests and 791 SDK tests
  pass on it (`NAPI_RS_FORCE_WASI=true`; it must be the string `true`, not `1`). The WebAssembly build has **no
  worker, file, HTTP or metrics sinks**: `FileTransport` keeps its JavaScript implementation and the metrics
  backends throw an error saying so.
- **The JavaScript redaction fallback now matches the core.** Twelve patterns, the core's leftmost-longest matching
  (applying patterns one after another let the phone pattern eat the digits of a Slack token), field classes and
  the depth rule: 74 of 74 corpus cases with and without the addon (it was 57 of 74 without).
- **Packaging.** Name `logquill`, an `exports` map with the same subpaths as the JavaScript package (`.`,
  `./langchain`, `./winston`, `./instrument`, `./browser`), a `files` allowlist, and platform packages named
  `@logquill/native-<platform>` (the loader looks for them). `npm run pack-smoke` packs the package and a platform
  package, installs both into a clean project and imports `logquill` by name as ESM and CommonJS; a second
  scenario installs only the main package and checks that the loader falls back to WebAssembly and that redaction
  works through it. It passes on Node 22, 24 and 26. The package remains `private` until a release is decided.
- **Parity.** Condition 1 (corpus): all 22 groups. Condition 2: `docs/stages/stage-6-traceability.md`. Condition 3
  (names): `docs/api-names.md`, Node column, including the `logquill/instrument` helpers and the pino and winston
  bridges.
- **Totals.** Addon: 913 tests, identical native, on WebAssembly, and on Node 22 / 24 / 26. SDK: 799 tests, 797 pass on
  the native path (2 skipped for the float limit), 782 with no addon, 792 on WebAssembly (the rest skipped because
  they need the runtime). `tsc`, `rustfmt` and `clippy -D warnings` are clean for the default and the core-only builds.

## Results since the second pass

- **Rust-backed sinks.** `sinks.rs` exposes the runtime's eight batching sinks; the SDK wraps them as `RustSyslogTransport`,
  `RustSQLiteTransport`, `RustDatadogTransport`, `RustNewRelicTransport`, `RustElasticsearchTransport`,
  `RustAppInsightsTransport`, `RustOTLPTraceTransport` and `RustOTLPLogsTransport` (a `NativeSink` base). They are
  alternatives to the JavaScript transports of similar names, which are unchanged and remain the default. Tested against a
  SQLite file read back with `node:sqlite`, a UDP socket, and local HTTP servers for Elasticsearch and OTLP; the
  fixed-endpoint cloud sinks (Datadog, New Relic, Application Insights) are only constructed and validated offline.
- **A real bug found by the corpus.** The SDK's `AlertingPlugin` silently dropped an alert when its table of tracked keys
  was full. Decision D44 says a full table never drops an alert; it now sends the alert untracked. 12 of 12 `alert-dedupe`
  corpus cases pass (`sdk/test/parity/corpus-alerting.test.ts`); an older test that asserted the dropping was changed.
- **Adapters, from the frameworks' published type declarations.** `OpenAIAgentsAdapter` is a duck-typed `TracingProcessor`
  for `@openai/agents` (read from `@openai/agents-core` 0.21 `tracing/processor.d.ts` and `spans.d.ts`): agent spans,
  tool calls, generations and responses with usage, handoffs, guardrails, errors. `LlamaIndexAdapter` listens on a
  `CallbackManager` for `llm-start`, `llm-end`, `llm-tool-call` and `llm-tool-result` (read from `@llamaindex/core` 0.6
  `global/dist/index.d.ts`). Neither imports its framework. **They are tested against objects built to those declarations
  (and Node's real `CustomEvent`), not against the installed libraries.**
- **`OTelMetricsBackend`**, over the OpenTelemetry Metrics API (a meter you pass, or the global one from the optional
  `@opentelemetry/api`), with a bounded instrument cache.
- **Ported Python cases for audit, metrics and graph** (`sdk/test/native/parity-extra.test.ts`, 20 tests): child chains,
  content policy, extra plugins, the metrics plugin's edge cases, StatsD, the pushgateway against a real server, session /
  parent / key rules. They found one more difference, now fixed: `FalkorDBTransport` selected its graph on every batch
  instead of once.
- **Totals.** Addon 913 (native and WebAssembly). SDK 853: 851 native, 810 with no addon, 836 on WebAssembly.

## Not done

- **Parity condition 2 for the Python tests** is a heuristic trace, not a proof: `stage-6-python-trace.md` (generated by
  `npm run trace-python-tests`) matches 775 legacy tests to Node tests by title words: 432 matched, 45 mapped by hand
  (queue, shutdown, exceptions, timestamps, trace tree), 123 not applicable, 175 unmatched. The unmatched list is the
  honest remainder; many are covered under differently worded tests, but that is not asserted. Ports written for the
  worst gaps this stage: `opt`, MCP, adaptive sampling, tamper chain and signed heads, content policy, config, syslog,
  trace tree (`sdk/test/core/opt.test.ts`, `sdk/test/instrument/mcp.test.ts`, `sdk/test/native/{core-ports,ports-2}.test.ts`).
  The MCP port found a real bug: `inbound()` restored the trace header after an `await`, so the caller kept the
  client's trace id; fixed with `withTraceparent()`.
- **Publishing.** The package is `private`; no release, no licence field (an open decision, plan section 8), and platform
  packages for the other five targets were never built (only the build machine's was exercised). Node 20 is unsupported.
- **Benchmarks** against the JavaScript package cover only the file path (about 17x faster end to end, 3.5x more
  caller-side time per call).
- **CI** (`node` job, including the WebAssembly build, the Node 22 / 24 runs and the pack test) ran on GitHub once the
  branch was pushed and is green on Ubuntu and macOS (runs above). Windows is not in the matrix.

## Open points for later slices

- **Oracle.** The JS 2.0 line is unreleased, so the oracle is the `logquill-js` working tree copied into
  `sdk/` (it carries uncommitted instrumentation work there as of 2026-10-28). Re-sync or pin a commit before
  the parity test in slice 7.
- **Licence.** Settled in Stage 9: MIT, in `package.json`, `Cargo.toml` and a `LICENSE` copy.
- **Hash chain and floats.** Chain input must reach the core as text; slice 2's native conversion has to keep an
  integer-valued JS number an integer and decide how a float with an integral value (`2.0`) is spelled, since
  JavaScript cannot tell them apart.
- **CI.** Resolved: the `node` job ran green on GitHub and has the Node LTS runs and the ESM / CJS consumer smoke
  test (slice 7).
- **Binding names.** The package is private `logquill-node` for now; the registry names (`logquill`,
  `@logquill/native-<platform>`) are set when the SDK exists.
