# Stage 8 — the medium tier

Goal: LogQuill from Go, the JVM, Swift, .NET and Ruby, on the Rust core, through one stable C ABI. Plan:
`docs/rust-core-plan.md` (section 5 "Language tiers", Stage 8). Code: `crates/logquill-ffi/` and `bindings/<language>/`.
Publishing: `docs/publishing.md` sections 8 to 12.

## Status: complete (slices 1 to 7 done, slice 8 partly); the CI jobs ran on GitHub and are green on Ubuntu and macOS

Branch `feat/stage-8-ffi`, cut from the Stage 7 work. Nothing here is committed or pushed.

Stage 8 is six deliverables on six toolchains. The plan's exit criteria per language are *corpus green on every OS the platform
supports*, *install from the registry into a clean project works* and *the README example is run in CI*. What was met:

- **Corpus:** the whole stateless corpus (815 checks, 15 of the 22 groups) runs through the C ABI itself, via Deno's FFI and the
  shared harness. Each language also runs the `canonical-json` group and its own behaviour tests. The seven stateful groups
  (sampling, rate limit, adaptive sampling, flight recorder, run summary, run plugin, alert de-duplication) are **not** run
  through the C ABI, because the ABI's first logger config does not expose those plugins; they remain covered by the Rust core
  tests and the Node, Python and WASM bindings. The harness lists skipped groups by name, and they account for exactly the 98
  missing checks (913 total).
- **Install from the registry:** impossible until something is published (nothing is published yet). The
  substitute used for every language is the one Stages 6 and 7 used: pack the real artefact (gem, NuGet package, jar, XCFramework
  package, Go module), install it into an empty project from a local source, use it by name with `LOGQUILL_LIB` unset.
- **README example run in CI:** every README example is a real file that is run, and each test suite checks the README contains
  it verbatim. The CI jobs for them ran on GitHub with PRs #34 and #35 and passed on Ubuntu and macOS (after two fixes to the workflow: a user-install for gems, and Go on macOS).
- **Every OS the platform supports:** only macOS arm64 was run. The CI matrix adds Linux; Windows is untried.

## Slices

| # | Slice | State |
|---|---|---|
| 1 | `logquill-ffi`: the C ABI, `Logger` handle, `lq_call` helpers, generated header, Rust and C tests, shared corpus through the ABI | **done** |
| 2 | Ruby gem (`ffi`) | **done** |
| 3 | .NET (P/Invoke) | **done** |
| 4 | Go (wazero + `core.wasm`) | **done** |
| 5 | Swift (C ABI as an XCFramework) | **done** |
| 6 | JVM (JNA over the C ABI) | **done** |
| 7 | CI, READMEs, publishing docs | **done** |
| 8 | Stateful plugins in the C ABI | **started**: `sampling`, `rate_limit`, `adaptive_sampling` done in `logquill-ffi`; not yet in the language wrappers |

## Results

| | tests | pack / consumer gate |
|---|---|---|
| `logquill-ffi` (Rust) | 16 ABI tests, 5 `lenient` tests in the core; fmt, clippy (both configurations) clean | C program compiled with `-Wall -Wextra -Werror` against the generated header; header freshness check |
| corpus through the C ABI | 815 / 815 (Deno FFI); a broken adapter fails 36 | |
| Ruby | 21 + 2 (README) | platform gem built, installed into an empty gem home, used by name |
| .NET | 20 on `net10.0` (library also compiles for `net8.0`) | `.nupkg` with `runtimes/osx-arm64/native/`, installed from a local feed into a new app |
| Go | 20 tests, race-clean; log call **6.0 µs**, filtered call 1.3 µs, helper 1.6 µs | a fresh module requires this one by import path and runs |
| Swift | 104 checks (`swift run LogQuillChecks`) | the README example runs; a fresh package depends on this one by path |
| JVM | 19 tests | jar with the native library published to a local Maven repo; a fresh Gradle app resolves it by coordinates |

## Design decisions

- **One C ABI, small and stable (`logquill-ffi`).** A `Logger` handle (`lq_logger_new`, `lq_log`, `lq_logger_child`,
  `lq_logger_set_level`, `lq_logger_flush`, `lq_logger_close`), a `Chain` handle, buffer allocation for WebAssembly hosts, and
  **one generic call for every stateless helper**: `lq_call(name, args_json) -> result_json`. Adding a helper changes no symbol, so a
  wrapper written today still links against next year's library, and a wrapper is a few dozen lines. The cost is that arguments
  are not typed at the C level; each wrapper maps names to idiomatic methods, and the shared corpus is what checks the mapping.
- **Strings are UTF-8, NUL-terminated; JSON is the data format**, as in the Node and WASM bindings: a host number cannot tell `1`
  from `1.0`, and the tamper chain hashes the spelling. Binary keys cross as base64 (through `lq_call`) or as bytes (the chain handle).
- **Errors never unwind across the boundary.** Every entry point catches panics; a failing call returns NULL / -1 and an owned
  message through `char **error`. Null pointers and invalid UTF-8 are errors, not crashes. A logger config with an unknown key is
  an error, so a typo cannot quietly disable redaction.
- **`lq_log` returns the finished record**, so a host-language transport can write it, and also queues it to the Rust transports in
  the logger's config (console, file). **Host-language plugins that change a record are therefore not possible** (the Rust
  transports have written it by then); every binding offers host *transports* instead, and Go, which writes its own records,
  offers host plugins too.
- **The runtime is a feature.** With `runtime` off (`wasm32-wasip1`) the logger is the pipeline only: no threads, files or sockets.
  That is the 1.5 MB `core.wasm` that Go embeds.
- **A log call never raises, in any language.** Each wrapper turns values JSON cannot carry (NaN, cycles, getters that throw, lone
  surrogates, invalid UTF-8, `NSException`-raising values in Swift) into text, and reports a failure on standard error.
- **Hash chain order.** For Rust transports the chain is applied on the worker thread as each record is written (decision D22). Go
  stamps under its own write lock, so its chain order is write order too.

## Deviations from the plan (for your decision)

1. **JVM and Swift use the C ABI, not UniFFI.** The plan said UniFFI Kotlin and UniFFI Swift. With the C ABI already built for .NET
   and Ruby, JNA and Swift's C interop reach the same artefacts (a jar, an XCFramework) without a second generator and a second
   interface to keep in sync; the cost is hand-written idiomatic wrappers rather than generated ones, and JNA's call overhead
   (not measured). If you want UniFFI for generated Kotlin and Swift later, the C ABI wrappers can stay as the fallback.
2. **Swift's checks are an executable, not an XCTest target**, because neither XCTest nor Swift Testing is in the Command Line
   Tools. They run wherever Swift runs; with full Xcode they could be moved into a test target.
3. **The JVM wrapper is Java, not Kotlin**, so that Kotlin and Java callers both use it; Kotlin extensions are a follow-up.

## Found along the way

- A shared-core gap: the lenient JSON reader (`NaN` / `Infinity`) existed only as copies in the Node and WASM glue. It is now in
  `logquill-core` (`parse_lenient`, with tests) and used by the FFI, the Node addon and the WASM module (the two older copies
  were removed in Stage 9; both corpora still pass 913 / 913).
- Ruby, .NET, Go, Swift and the JVM each had a "never raises" bug the test for it caught: Ruby binary strings in `meta`; Swift's
  `JSONSerialization` raising an uncatchable Objective-C exception; .NET getters that throw and lone surrogates.
- `wasm-opt` (Stage 7) and XCTest (here) are both unavailable or unusable in this environment; each decision is recorded where it
  was made.

## Open points

- **CI** ran on Ubuntu and macOS and passed (`ffi`, `ruby`, `dotnet`, `go`, `swift`, `jvm`), which covers Linux and
  `xcodebuild -create-xcframework`. `net8.0` is still compile-checked only (the tests run on `net10.0`). **Windows** is not in the matrix and has never run.
- **No registry publishing.** Maven's group ID `io.logquill` is a placeholder; the Go module's `core.wasm` is now committed (Stage 9); the Swift binary target needs a zipped multi-platform XCFramework and
  checksum.
- **Slice 8, what is left.** The logger config now takes `sampling` (`rate`), `rate_limit` (`max_records`, `per_seconds`, `key`,
  `max_keys`) and `adaptive_sampling`; the logger supplies the clock and a clock-seeded xorshift random source (3 new ABI tests;
  the shared corpus does not cover them because its groups call the core types with explicit draws). Still missing: **tail
  sampling and the flight recorder** (they release earlier records, which a pipeline step cannot do, so the logger needs a
  second write path), the **run summary** and **alert de-duplication** (they emit new records and alerts on a timer), the
  `Plugins.*` helpers in the five language wrappers (the config JSON can carry them already), and docs in each README.
- **Not in any medium-tier binding:** HTTP, cloud and database transports, standard logging bridges (`log/slog`, SLF4J, `swift-log`,
  `Microsoft.Extensions.Logging`, Ruby's `Logger`), framework adapters and SDK patching, Android, iOS and Windows builds.
- **Go's file transport** rotates by size (`NewRotatingFileTransport`, Stage 9) but does not compress rotated files; the Rust file
  transport used by the other bindings also offers `gz` / `zip`.
- **Per-call cost** was measured for Go (`go test -bench`) and the JVM (`gradle bench`, Stage 9), on the build machine:

  | | log call (redact + PII) | call below the level | stateless helper |
  |---|---|---|---|
  | Go (wazero) | 6.0 µs | 1.3 µs | 1.6 µs |
  | JVM (JNA, Jackson) | 7.0 µs | 4.9 µs | 3.4 µs |

  The JVM filtered call is slow because the wrapper serialises `meta` to JSON before the call, so the level filter in Rust cannot
  save that work. A check of the level in Java first would fix it; it is not done. Ruby, .NET and Swift were not benchmarked.
