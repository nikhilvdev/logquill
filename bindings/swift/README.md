# LogQuill for Swift

Structured logging for Swift on LogQuill's Rust core, through its C ABI (`crates/logquill-ffi`), shipped as a Swift package with
an XCFramework. The same record shape, the same redaction and the same tamper-evident hash chain as the Python, Node, browser
and other bindings: redaction and the level filter run in Rust before anything is written.

> **Not published yet.** The package is unreleased until the licence and release plan are settled
> (`docs/rust-core-plan.md`, section 8). See [Build and test](#build-and-test) to use it from this repository.

## Use it

```swift
import LogQuill

var options = LoggerOptions()
options.name = "web"
options.plugins = [Plugins.redact(), Plugins.pii()]
options.transports = [Transports.file(path: "web.log")]
let log = try Logger(options)

log.info("signed in", ["user": "alice@example.com", "password": "hunter2"])
try log.child("db", ["component": "pg"]).warn("slow query", ["ms": 840])
log.close()
```

`web.log` now has two JSON lines. In the first, `password` is `"***"` and the address in `user` is `***`; the second has
`"logger": "web.db"` and `component` and `ms` in `meta`. Call `close()` (or `flush()`) before the process ends: records are
written on a background thread.

### The logger

- `Logger(LoggerOptions)`: `name`, `level` (default `.info`), `plugins`, `transports`, `hostTransports`, `contentPolicy`,
  `tamperEvident`, `queue`. A wrong option throws `LogQuillError` saying which one and what is allowed; an unknown option is an
  error, not ignored, so a typo cannot quietly leave a secret in the log.
- `log.info(message, meta, llm:)` and the same for `trace debug warn error fatal`. `meta` is a `[String: Any]`; `llm` is an
  LLM-call block (`["model": "...", "tokens_in": 3]`). Returns `true` when the record was kept and `false` when it was filtered
  (the result is discardable). `logRecord(...)` returns the finished record as JSON text instead (nil if filtered). **A log call
  never throws or crashes**: NaN, `Date`, `URL`, non-string dictionary keys and arbitrary objects are turned into JSON, where
  `JSONSerialization` would raise an Objective-C exception that Swift cannot catch.
- `log.child("db", ["component": "pg"])` is a logger named `web.db` with that meta on every record, sharing the level and
  transports. `log.setLevel(.error)` changes it for the logger, its parent and its children.
- `Plugins.redact(keys:classes:replacement:)` and `Plugins.pii(replacement:)` run in Rust, in order. `contentPolicy` is `off`
  (the default: prompt, completion and tool content is withheld), `hash`, `truncate` or `full`. `tamperEvident = true` chains each
  record as it is written; check a log file with `LogQuillCore.verifyChain`.
- `transports` (`Transports.console(...)`, `Transports.file(path:...)`) are written by Rust on a background thread.
  `hostTransports` takes any `HostTransport`: `write(line:record:)` is called on your thread with the finished,
  already-redacted record; `flush()` and `close()` are optional.
- A `Logger` is thread-safe and frees its native resources when it is released.

### Context

```swift
LogContext.with(["request_id": "r-1"]) { log.info("handling") }       // request_id is in meta
await LogContext.with(["request_id": "r-1"]) { await work(log) }       // and across awaits
```

The context is a task-local value: it is there after an `await` and in child tasks created inside the scope, and not in
unrelated tasks. The call's own fields win over the context.

### Helpers and the hash chain

`LogQuillCore.call("canonicalJson", [...])` runs any stateless helper of the core by the name the Node addon uses (`callRaw`
returns the JSON text); typed functions cover the common ones: `canonicalJSON`, `piiRedactText`, `redactMeta`, `verifyChain`,
`verifySignedChain`, `signHead`, `verifyHeadSignature`. A record, meta or list of records may be a value or a `String` of JSON
text; **pass text when a number's spelling matters**, since `1` and `1.0` hash differently in a chain. `Chain` stamps records and
signs its head:

```swift
let chain = try Chain()
let records = [try chain.stamp(recordOne), try chain.stamp(recordTwo)]
try LogQuillCore.verifyChain("[" + records.joined(separator: ",") + "]")          // ok == true, recordsChecked == 2
try LogQuillCore.verifySignedChain(json, key: key, signature: try chain.signHead(key: key))   // also catches a deleted last line
```

## Not in this package (yet)

The medium-tier bindings ship the facade, context, console / file transports and Swift transports. Not here: HTTP, cloud and
database transports (write a `HostTransport`), `swift-log` and `OSLog` integration, framework adapters, and the stateful
plugins (sampling, rate limiting, adaptive sampling, the flight recorder, the run summary): the C ABI's first logger config does
not carry them. Host-language *plugins* that change a record are not possible either: the Rust transports have written it by
the time Swift sees it; use a `HostTransport` to act on a record. The XCFramework built here is for the machine's own
architecture on macOS; a release builds the other Apple platforms and architectures.

## Build and test

You need Swift 5.9 or newer and a Rust toolchain. From the repository root:

```sh
bindings/swift/scripts/build-xcframework.sh     # builds CLogQuill.xcframework (the C ABI as a static library plus header)
cd bindings/swift
swift build
swift run LogQuillChecks                        # 104 checks
scripts/smoke.sh                                # runs the example above, checks this README has it, builds a fresh package on this one
```

The checks are an executable (`LogQuillChecks`) rather than an XCTest target because neither XCTest nor Swift Testing is in the
Xcode Command Line Tools; this way they run wherever Swift does. With full Xcode the package also builds in Xcode and
`build-xcframework.sh` uses `xcodebuild -create-xcframework`. The XCFramework is untracked (a build product); a release
attaches a zipped one with its checksum (`swift package compute-checksum`) and points the binary target at it.
