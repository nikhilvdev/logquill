# Stage 7 — WASM and browser

Goal: `npm install @logquill/wasm` gives the browser, Deno, Bun and Cloudflare Workers the Rust core's logic (redaction,
PII, tamper chain, content policy, formatters, ids, sampling) behind the same JSON-text boundary the Node addon uses,
plus a small SDK for the parts a browser can do (console, beacon, stack-based spans).
Plan: `docs/rust-core-plan.md` (sections 3 and 5, Stage 7). Spec: `docs/spec/07-adapters-instrumentation-bridges.md`
("Browser build"). Code: `bindings/wasm/`.

## Status: complete (all five slices); the `wasm` CI job is green on GitHub (Ubuntu and macOS)

Branch `feat/stage-7-wasm`, cut from `develop` after Stage 6 (PR #33) merged. Nothing here is committed or pushed.

## What already exists (so this stage does not redo it)

- Stage 6 shipped a pure-JavaScript browser entry, `logquill/browser` (`bindings/node/sdk/src/browser.ts`): core logger,
  formatters, context, redact / PII / sampling plugins, `ConsoleTransport`, `BeaconTransport`, stack-based span and
  context, and a test that fails if a Node built-in is imported. It has **no Rust core**: in a browser every plugin
  runs its JavaScript fallback (`native-browser.ts` returns no addon).
- The Node addon's own WebAssembly build (`wasm32-wasip1-threads`) is a fallback for Node, not a browser target: it
  needs WASI and, for threads, `SharedArrayBuffer` with cross-origin isolation.

So Stage 7 adds the thing that is missing: the core compiled for `wasm32-unknown-unknown` with wasm-bindgen, loadable
without WASI or threads in any host, and wires the browser SDK to it.

## Layout decisions

- `bindings/wasm/` is **outside the Cargo workspace** (`exclude` in the root `Cargo.toml`), own lockfile, like the
  Python and Node bindings: wasm-bindgen's macros expand to `unsafe`, which the workspace forbids.
- Depends on `logquill-core` only. No `logquill-runtime`: no threads, files, sockets or SQLite in this target. Transports
  are the SDK's job (spec 05: "browser: SDK").
- **Same boundary as the Node addon:** pure helpers take and return JSON text, and the exported names are the Node
  addon's names (camelCase), so one corpus harness runs against both. The glue is written twice (napi and
  wasm-bindgen); it is thin (`parse`, call the core, map the error), and the shared corpus is what catches drift.
  Sharing it would mean refactoring the Node addon, which is out of scope here.
- Built with `wasm-bindgen` 0.2.129 (CLI and crate versions must match exactly), `--target web`: an ES module plus a
  `.wasm` file; the host passes the module or a URL to `init()`. This is the one shape that works in browsers, Deno
  and Workers (which import the `.wasm` as a compiled module and cannot fetch at runtime).
- No Node built-ins anywhere in the package; the existing "no Node built-ins" test is extended to it.
- Concurrency caveat stays as the spec states it: span nesting and bound context use a plain stack, correct for one
  open block at a time, not isolated across concurrent awaits.

## Slices

| # | Slice | State |
|---|---|---|
| 1 | Crate scaffold, build script, pure helpers (`parseRecord`, `verifyChain`, `canonicalJson`, formatters, `parseLogfmt`, `utcTimestamp`); corpus groups `canonical-json` and `formatters` and the golden records, run on Node | **done** |
| 2 | Remaining core logic: redaction, PII, content policy, field classes, tamper chain and signed heads, ids and semconv, sampling and run stamp; every corpus group the Node addon passes | **done** |
| 3 | Same corpus in a headless browser (Chrome), Deno and Workers (workerd); Bun as well | **done** |
| 4 | Browser SDK on the wasm core: `Logger`, console and beacon transports, stack-based span; fallback to the JS plugins when the module is not loaded | **done** |
| 5 | Size and speed budget, CI job, README, install docs | **done** |

## Results so far

- **Slice 1.** `bindings/wasm` builds for `wasm32-unknown-unknown` and loads with `initSync` in Node; 266 tests pass from the
  Node binding's own files (`golden records`, the shared `hash chain`, corpus `canonical-json` and `formatters`), run
  against this build by setting `LOGQUILL_TARGET` (the two Node test files now import the module under test from that
  variable; default is the native addon, and the Node suite is unchanged at 913).
- **`SystemTime` panics on this target**, so `utcTimestamp` reads `Date.now()` through `js-sys`. The core's only clock use is
  `SystemClock`; everything else takes an injected clock, so later slices pass `Date.now()` the same way.
- **Size, slice 1 only:** 1.18 MB raw, 375 KB gzipped (`opt-level = "s"`, LTO, no `wasm-opt` yet). Too big to ship as is;
  slice 5 sets the budget and tries `wasm-opt`, stripping the name section and the lighter regex features.
- The corpus harness still uses Node-only pieces (`Buffer`, `node:fs`, `process.env`); slice 3 has to replace those to run
  it in a browser, Deno and Workers.

- **Slice 2.** All 22 corpus groups plus the golden records and shared chain pass against the WASM build in Node: the same
  913 tests the native addon passes, from the same files. Surface: `plugins.rs`, `state.rs`, `derive.rs`, mirroring the Node
  addon's modules (redactors, content policy, field classes, ECS / SQL / graph rows, tamper chain and signed heads, ids,
  semconv, trace trees, sampling, rate limit, adaptive sampling, flight recorder, run summary, run stamp, alert
  de-duplication). Classes take their options as JSON text and return structured results as JSON text; `js/index.mjs`
  turns those into the addon's option objects and plain objects. A bogus `LOGQUILL_TARGET` makes the run fail, so the
  pass is not the native addon answering. Six WASM-only tests (`tests/wasm.test.mjs`): `init` and `initSync`, the
  `Date.now()` clock, error shape, byte keys.
- **Size after slice 2:** 1.42 MB raw, 457 KB gzipped (was 1.18 MB / 375 KB). The budget work in slice 5 matters more now.
- Not exposed on purpose: everything in `dispatch`, `sinks`, `alerts`, `metrics`, `watchers` (they need the runtime crate:
  threads, files, sockets). The SDK supplies browser transports in slice 4.

- **Slice 3.** The same 913 checks pass in four hosts besides Node: **Chrome 156 headless** (module loaded with async `init`,
  `.wasm` fetched by URL), **Deno 2.9.6** (`initSync` from bytes), **Cloudflare Workers** (locally under workerd through
  `wrangler`'s `unstable_dev`; the `.wasm` imported as a precompiled module) and **Bun 1.4.4**. One harness serves all of
  them (`tests/hosts/harness.mjs`): no Node built-ins, no filesystem, fixtures packed into a generated module
  (`scripts/gen-fixtures.mjs`). To make that possible the Node binding's corpus mapping moved into
  `bindings/node/tests/corpus-groups.mjs` (and the pure parser into `lossless-core.mjs`); `corpus.test.mjs` now loops over
  it, so the native addon and every host run literally the same group definitions (Node suite unchanged at 913).
  - The harness **fails** (does not skip) where `JSON.parse` lacks source-text access, since the corpus cannot be read
    without it; all four hosts have it.
  - Negative control: a module with two small deliberate breaks fails 30 checks, so a pass means something.
  - `tests/no-node-builtins.test.mjs` reads what ships to those hosts (`js/index.mjs`, the wasm-bindgen glue, the harness,
    the shared corpus modules) and fails on a `node:` import, `require`, `process.*`, `__dirname`, or an unguarded
    `Buffer`. The host runs are the stronger proof; this catches it before one starts.
  - Run it all with `npm test` in `bindings/wasm` (build, the Node run, then each host); `test:deno`, `test:bun`,
    `test:chrome`, `test:worker` run one.
  - Not covered: Firefox and Safari (only Chrome is driven), and a real deployed Worker (workerd locally, same engine).
  - CI for these jobs is slice 5. Chrome needs to be installed on the runner; `CHROME_PATH` overrides the search.

- **Slice 4.** `@logquill/wasm` now exports the browser SDK at `.` (and the raw core at `./core`). It is the Node SDK's own
  browser entry, rebuilt (`npm run build:sdk`, tsup) with one swap: the native-addon loader (`#native` and the direct
  `core/native.js` imports) becomes `sdk/native-wasm.ts`, which hands back the WASM core once `initWasm()` has run.
  Nothing in the plugins changed, because the core's shim has the addon's names and shapes. So after `await initWasm()`:
  `RedactPlugin` and `PIIRedactPlugin` run in Rust, and three plugins that have no JavaScript version become available in a
  browser: `AdaptiveSamplingPlugin`, `FlightRecorderPlugin`, `RunSummaryPlugin`. Before it, or if it fails, every plugin
  keeps its pure-JavaScript path and the Rust-only ones throw a message saying to call `initWasm()`.
  - `initWasm(source?)` takes a URL or `Response` (a page), bytes, or a `WebAssembly.Module` (a Worker); with no argument it
    fetches `logquill_wasm_bg.wasm` from beside the bundle. `initWasmSync(source)` for hosts that can compile at load time.
    A second call is a no-op.
  - **A plugin chooses its path when it is constructed.** Build loggers after `await initWasm()`; one built earlier stays
    on JavaScript. Both paths give the same redaction (checked), so this costs speed only.
  - 16 SDK checks (`tests/hosts/sdk-harness.mjs`) run in all five hosts, 929 checks per host with the 913 core ones: before
    init (JavaScript paths, the refusal message), the logger (levels, child, agent kinds, nested spans, a throwing span,
    console routing, beacon batching), init (and its repeat), then the plugins on the core (redaction parity, PII, adaptive
    sampling, flight recorder, run summary). Chrome calls `initWasm()` with no argument, so the default fetch is exercised.
    A loader that claims success without loading fails 5 checks.
  - Bundle: 122 KB, 26.6 KB gzipped, no Node built-ins (the no-built-ins test now covers it, and accepts `process` /
    `Buffer` only behind a `typeof` guard, which `ConsoleTransport` uses). Plus the `.wasm` (1.42 MB; slice 5).
  - Not done: the Node package's `logquill/browser` still has no core (it cannot load a `.wasm` without choosing a
    mechanism); a user who wants Rust in a browser installs `@logquill/wasm`.

- **Slice 5.**
  - **Size, 1.42 MB / 457 KB gzipped -> 820 KiB / 319 KB gzipped** (-30% gzipped, -41% raw), two changes:
    stripping the name and producers sections in the build (`--remove-name-section --remove-producers-section`, 28 KB
    gzipped), and **leaving the `regex` crate's Unicode tables out of the core** (88 KB gzipped). The second is a change
    to the shared core and is safe because every pattern in it is ASCII-only (decision D41: explicit classes and
    `(?-u:\b)`); it was re-verified on everything that builds on the core: `cargo test --workspace`, the Node addon
    (913) and SDK (921), the Python binding (294, including the 24 oracle tests against published 2.0.0), and WASM in
    all hosts.
  - **Regex engine features, measured, and why `perf-dfa` stays.** Without the lazy DFA, scanning is 28 to 55 times
    slower: 112 us instead of 4 us for a small record, 1.7 ms instead of 32 us for 4 KB of prose. A second 22 KB gzipped
    is not worth losing that. So the core has two features: `regex-perf` (default; all of `regex/perf`, what the
    native bindings get, unchanged speed) and `regex-perf-web` (`perf-literal` + `perf-dfa`; what `bindings/wasm` asks
    for with `default-features = false`).

    | regex features | gzipped | PII scan, 4 KB |
    |---|---|---|
    | `std` only | 244 KB | 1.7 ms |
    | `std` + `perf-literal` | 286 KB | 454 us |
    | `std` + `perf-literal` + `perf-onepass` + `perf-backtrack` | 296 KB | 163 us |
    | **`std` + `perf-literal` + `perf-dfa` (web)** | **319 KB** | **32 us** |
    | `std` + `perf` (everything) | 341 KB | 32 us |
  - **`wasm-opt` was tried and dropped.** The Binaryen the npm `wasm-opt` package ships emits a module V8 rejects
    ("invalid value type"), and it saved nothing gzipped (430.0 KB vs 429.2 KB). My first size figures were for files
    I had not loaded; every figure above is from a module that was instantiated and benchmarked.
  - **Budget** (`budget.json`, `scripts/check-budget.mjs`, `npm run check:budget`): size to within about 8%, speed to
    7 to 10 times the measured figures (so a slow CI runner does not fail it, while losing the DFA does). Checked
    with a tightened budget: two lines go OVER and the exit is non-zero. V8 caches compiled modules inside a process,
    so a "median of 7 compiles" was really cache hits; `bench.mjs` reports the one cold compile (about 1 ms).
  - **CI** (`wasm` job in `.github/workflows/ci.yml`, Ubuntu and macOS): installs the pinned `wasm-bindgen` 0.2.129
    from its GitHub release checked against a pinned SHA-256 (matches the publisher's own `.sha256sum`), `npm ci
    --ignore-scripts`, `npm rebuild bun`, then `npm test`, `tsc`, `cargo fmt`, clippy. **Never run on GitHub** (it
    needs a push). I ran the same recipe from a clean copy of the tree, with `CI=1`; that found a real problem: Bun's
    launcher links its binary in a postinstall script, so `npm ci --ignore-scripts` alone fails the Bun host. Fixed with
    `npm rebuild bun`. The Linux binary and Chrome's sandbox (`--no-sandbox` is passed when `CI` is set) are untested here.
  - **README** (`bindings/wasm/README.md`) with install, usage per host, what is and is not in the build, the
    caveats (spans on a stack, CSP needs `'wasm-unsafe-eval'`, no background thread), and size and speed. Its three
    examples are real files in `examples/`; `npm run test:examples` runs each in its host (Deno, Bun, headless Chrome, a
    Worker under workerd), checks it prints redacted output, and fails if the README stops containing the files
    verbatim. Writing the README caught two of my own errors (which plugins run on the core; how dispatch is
    scheduled).
  - **Install docs:** `docs/publishing.md` gets section 7b for `@logquill/wasm` and a correction: the Node WebAssembly
    fallback ships inside `logquill`, not as `@logquill/wasm`. `npm run pack-smoke` is the gate: it packs the tarball,
    fails on a stray file or a missing `exports` target, installs it into an empty project and uses it by name.

## Exit criteria (from the plan)

Corpus passes in headless browser, Deno and Workers; the "no Node built-ins" test is kept.

## Open points

- **The module ships once** (fixed in Stage 9): `./core` now defaults to `dist/logquill_wasm_bg.wasm`, the file the SDK loads, and
  `pkg/logquill_wasm_bg.wasm` is no longer in the tarball; `pack-smoke` fails if two `.wasm` files ship.
- **CI** ran on GitHub with the Stage 8 pull request (PRs #34, #35): the `wasm` job passed on Ubuntu and macOS, including the pinned `wasm-bindgen` download and headless Chrome. Windows is not in the matrix.
- **Not tested:** Firefox and Safari; a deployed Worker (workerd locally is the same engine).
- **The glue is written twice** (napi in `bindings/node`, wasm-bindgen here); the shared corpus catches drift, but it
  is a cost every new core function pays.
- **Concurrent spans** share a stack in a browser (spec trade-off, documented in the README).
- Licence and publishing: MIT (Stage 9); the package is no longer `private`, but nothing is published.
