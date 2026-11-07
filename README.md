# LogQuill

[![CI](https://github.com/nikhilvdev/logquill/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/nikhilvdev/logquill/actions/workflows/ci.yml?query=branch%3Amain)
[![Release](https://github.com/nikhilvdev/logquill/actions/workflows/release.yml/badge.svg)](https://github.com/nikhilvdev/logquill/actions/workflows/release.yml)
[![License](https://img.shields.io/github/license/nikhilvdev/logquill)](LICENSE)
[![GitHub tag](https://img.shields.io/github/v/tag/nikhilvdev/logquill)](https://github.com/nikhilvdev/logquill/tags)

[![crates.io](https://img.shields.io/crates/v/logquill-core.svg)](https://crates.io/crates/logquill-core)
[![PyPI](https://img.shields.io/pypi/v/logquill.svg)](https://pypi.org/project/logquill/)
[![Python versions](https://img.shields.io/badge/python-3.10%2B-blue.svg)](bindings/python/pyproject.toml)
[![Downloads](https://static.pepy.tech/badge/logquill)](https://pepy.tech/project/logquill)
[![npm](https://img.shields.io/npm/v/logquill.svg)](https://www.npmjs.com/package/logquill)
[![npm wasm](https://img.shields.io/npm/v/@logquill/wasm.svg)](https://www.npmjs.com/package/@logquill/wasm)
[![Gem](https://img.shields.io/gem/v/logquill.svg)](https://rubygems.org/gems/logquill)
[![NuGet](https://img.shields.io/nuget/v/LogQuill.svg)](https://www.nuget.org/packages/LogQuill)
[![Maven Central](https://img.shields.io/maven-central/v/io.github.nikhilvdev/logquill.svg)](https://central.sonatype.com/artifact/io.github.nikhilvdev/logquill)
[![Go Reference](https://pkg.go.dev/badge/github.com/nikhilvdev/logquill/bindings/go/v3.svg)](https://pkg.go.dev/github.com/nikhilvdev/logquill/bindings/go/v3)

Structured logging for services and AI agents: one Rust core, one record shape, and an
SDK for each language, so a team running several languages gets one mental model and one
JSON log format.

**Status: 3.0.0 is the current release, published to crates.io, PyPI, npm, RubyGems, NuGet, Maven Central, the Go module proxy and Swift Package Manager.** The previous releases are `logquill` 2.x for Python (PyPI, `main` branch)
and `logquill` 1.x for Node (the separate `logquill-js` repository). Every binding's README says how to install it.

## What you get

- **One record shape everywhere.** Every record is
  `{"schema_version", "timestamp", "level", "logger", "message", "meta", "llm"?}`, defined by
  `schema/record.schema.json` and checked in every language against the same golden records
  and a shared corpus of 865 hostile-input cases (`schema/`).
- **Six levels** — `TRACE(5) DEBUG(10) INFO(20) WARN(30) ERROR(40) FATAL(50)` — with the same
  names and weights in every language, plus child loggers, per-call options and runtime level control.
- **Agent tracing.** `thought` / `action` / `observation` / `decision` records, spans with
  durations, an `llm` block (model, tokens, cost, latency, finish reason), retry tracking,
  state diffs, W3C trace-context propagation, and OpenTelemetry GenAI export.
- **Privacy by default.** Prompt, completion and tool content is withheld unless you opt in
  (`off` / `hash` / `truncate` / `full`); key-based and PII redaction run in Rust before any
  transport sees a record; a failing redactor drops the record rather than leaking it.
- **Tamper evidence.** Hash-chained records, an optional signed chain head, and
  `logquill verify` to check a log after the fact.
- **Bounded memory.** A bounded queue and one worker thread with `drop_oldest`, `drop_newest`
  or `block` backpressure. A log call never raises.
- **Local-first debugging.** `logquill tail`, `trace`, `dev` and `serve` read a JSONL file or a
  SQLite log database on your machine: no account, no backend.

## Packages

| Package | Registry | Source | Built on |
|---|---|---|---|
| `logquill-core`, `logquill-runtime`, `logquill-ffi`, `logquill-cli` | crates.io | `crates/` | Rust |
| `logquill` (Python 3.10+) | PyPI | `bindings/python` | PyO3, `abi3` wheel |
| `logquill` (Node 22+, TypeScript) | npm | `bindings/node` | napi-rs addon, WebAssembly fallback |
| `@logquill/wasm` (browsers, Deno, Bun, Workers) | npm | `bindings/wasm` | core as WebAssembly |
| `logquill` | RubyGems | `bindings/ruby` | C ABI, `ffi` gem |
| `LogQuill` | NuGet | `bindings/dotnet` | C ABI, P/Invoke |
| `LogQuill` (Java, Kotlin, Scala) | Maven Central (`io.github.nikhilvdev`) | `bindings/jvm` | C ABI, JNA |
| `…/bindings/go/v3` | Go module proxy | `bindings/go` | core as WebAssembly in wazero, no cgo |
| `LogQuill` | Swift Package Manager | `bindings/swift` | C ABI, XCFramework |

All packages share one lockstep version (currently `3.0.0`). [`docs/releasing.md`](docs/releasing.md) has the release process.

## Quickstart

Each snippet logs a record carrying a secret and an email. Where the redact and PII plugins are
on, `password` is written as `"***"` and the address in `user` is masked.

**Python** (`bindings/python`)

```python
from logquill import Level, Logger

logger = Logger("app", level=Level.INFO)
record = logger.info("user signed up", user_id=42, plan="pro")
logger.set_level("debug")
```

**Node / TypeScript** (`bindings/node`)

```ts
import { Logger, FileTransport } from "logquill";

const log = new Logger("api", {
  transports: [new FileTransport("app.log", { maxBytes: 10_000_000, compression: "gz" })],
  meta: { service: "api" },
});
log.info("signed up", { user_id: 42 });
await log.close();
```

**Browser, Deno, Bun, Workers** (`bindings/wasm`)

```js
import { initWasm, Logger, ConsoleTransport, RedactPlugin, PIIRedactPlugin } from "@logquill/wasm";

await initWasm(); // load the module once, before building loggers
const log = new Logger("web", {
  transports: [new ConsoleTransport({ colorize: false })],
  plugins: [new RedactPlugin(), new PIIRedactPlugin()],
});
log.info("signed in", { user: "alice@example.com", password: "hunter2" });
await log.flush();
```

**Ruby** (`bindings/ruby`)

```ruby
require "logquill"

log = Logquill::Logger.new(
  name: "web",
  plugins: [{ type: "redact" }, { type: "pii" }],
  transports: [{ type: "file", path: "web.log" }]
)
log.info("signed in", user: "alice@example.com", password: "hunter2")
log.close
```

**.NET** (`bindings/dotnet`)

```csharp
using LogQuill;

using var log = new Logger(new LoggerOptions
{
    Name = "web",
    Plugins = { PluginConfig.Redact(), PluginConfig.Pii() },
    Transports = { TransportConfig.File("web.log") },
});
log.Info("signed in", new { User = "alice@example.com", Password = "hunter2" });
```

**Java / Kotlin** (`bindings/jvm`)

```java
LoggerOptions options = LoggerOptions.builder()
        .name("web")
        .plugin(Plugins.redact())
        .plugin(Plugins.pii())
        .transport(Transports.file("web.log"))
        .build();
try (Logger log = new Logger(options)) {
    log.info("signed in", Map.of("user", "alice@example.com", "password", "hunter2"));
}
```

**Go** (`bindings/go`)

```go
log, err := logquill.New(
	logquill.WithName("web"),
	logquill.WithPlugins(logquill.Redact(nil, nil, ""), logquill.PII("")),
	logquill.WithTransports(logquill.NewConsoleTransport(os.Stdout, os.Stderr)),
)
if err != nil {
	panic(err)
}
defer log.Close()
log.Info("signed in", logquill.Meta{"user": "alice@example.com", "password": "hunter2"})
```

**Swift** (`bindings/swift`)

```swift
import LogQuill

var options = LoggerOptions()
options.name = "web"
options.plugins = [Plugins.redact(), Plugins.pii()]
options.transports = [Transports.file(path: "web.log")]
let log = try Logger(options)
log.info("signed in", ["user": "alice@example.com", "password": "hunter2"])
log.close()
```

Records are written on a background thread, so call `close()` (or `flush()`) before the
process ends. Method names follow each language's casing; [`docs/api-names.md`](docs/api-names.md) is the full table.
These snippets are trimmed from the READMEs under `bindings/`, whose examples are run by each
binding's tests.

## What the core provides

**Transports** (native, on the Rust worker): console, rotating file (size rotation, `gz` / `zip`
compression, Fernet encryption at rest), syslog, SQLite, batched HTTP, Datadog, New Relic,
Elasticsearch (ECS-mapped), Application Insights, and OTLP traces and logs. The Python and Node
SDKs add cloud, SQL, NoSQL, queue and graph (Neo4j, FalkorDB, Neptune) transports; see each
binding's README for what it carries. The Ruby, .NET, Go, Swift and JVM bindings can also register
a host transport written in their own language.

**Plugins**: key and class-based redaction, PII redaction, sampling, rate limiting, adaptive
sampling with a byte-rate budget, flight recorder (a failing run ships its DEBUG records, a
passing one none), run summary, tamper-evident hash chain, Slack / PagerDuty / email alerts with
deduplication, and a metrics bridge (StatsD, Prometheus pushgateway, OTLP).

**Agent features**: `llm_call` with token, cost and latency fields; spans; run, trace and thread
correlation ids; OpenTelemetry GenAI conventions behind one pinned mapping module; and an
`AuditLogger` profile (content off, secrets and PII redacted, every record hash-chained). The
Python and Node SDKs also carry the framework adapters and provider auto-instrumentation.

Where a binding lacks something the Python or Node SDK has, its README says so; the shared
fixtures in `schema/` define what every binding must do.

## The `logquill` command

Built from `crates/logquill-cli` with cargo.

```bash
logquill tail app.log --level=error           # filter by level; -f follows, -n 50 shows the last 50, --json prints raw lines
logquill trace <run_id> --file app.log        # one run's span tree with duration, token and cost annotations
logquill dev app.log                          # redraw the latest run's span tree live as the file grows
logquill serve --file app.log                 # local web viewer; or --db logs.sqlite [--table logs]
logquill verify app.log                       # check a hash-chained log was not edited, reordered or truncated
logquill verify app.log --sign-key HEX --signature HEX   # also check the signed chain head
```

The viewer binds to `127.0.0.1` by default; any other `--host` exposes your logs to the network.
`tail`, `trace` and `dev` accept `--no-color` for plain output.

## Built for production

| Concern | What you get | Details |
|---|---|---|
| Stability | One lockstep version across every language, Semantic Versioning, a defined stability promise | [Versioning](https://github.com/nikhilvdev/logquill/blob/main/docs/versioning.md) |
| Privacy | Prompt and tool content off by default; secrets and PII redacted in Rust before any transport; a failing redactor drops the record | [Contract](https://github.com/nikhilvdev/logquill/blob/main/docs/contract.md) |
| Integrity | Hash-chained, optionally signed logs; `logquill verify` detects an edited or removed line | [Contract](https://github.com/nikhilvdev/logquill/blob/main/docs/contract.md) |
| Reliability | A log call never raises; bounded queue and batches; worker survives `fork`; measured in CI | [Architecture](https://github.com/nikhilvdev/logquill/blob/main/docs/architecture.md) |
| Supply chain | Tag-only releases, trusted publishing, provenance on npm and PyPI, pinned actions | [Supply chain](https://github.com/nikhilvdev/logquill/blob/main/docs/supply-chain.md) |
| Observability | OpenTelemetry GenAI spans and logs, W3C trace context, Kibana dashboard for ECS | [Contract](https://github.com/nikhilvdev/logquill/blob/main/docs/contract.md) |
| Support | Supported versions and how to get help | [SUPPORT](https://github.com/nikhilvdev/logquill/blob/main/.github/SUPPORT.md) |

**Compatibility.** Python 3.10+, Node 22+, .NET `net8.0` and `net10.0`, and browsers, Deno, Bun and Workers through
WebAssembly. Requirements for Ruby, JVM, Go and Swift are in each binding's README under `bindings/`.

## Layout

| Path | What it is |
|---|---|
| `crates/logquill-core` | Pure logic: records, levels, privacy, integrity, processing and the log-call pipeline. No I/O, no threads. |
| `crates/logquill-runtime` | The bounded queue, the worker thread, and the console, file, syslog, SQLite and HTTP-family transports. |
| `crates/logquill-cli` | The `logquill` command: `tail`, `trace`, `dev`, `serve`, `verify`. |
| `crates/logquill-ffi` | The C ABI (`include/logquill.h`) that the Ruby, .NET, Go, Swift and JVM bindings use. |
| `crates/logquill-runtime-checks` | Runtime checks that need `unsafe` (a real fork, a counting allocator). Not published. |
| `bindings/python` | Python: a compiled `abi3` wheel (PyO3). |
| `bindings/node` | Node and TypeScript: a napi-rs addon, with a WebAssembly fallback. |
| `bindings/wasm` | Browsers and edge runtimes: the core as WebAssembly. |
| `bindings/{ruby,dotnet,go,swift,jvm}` | The medium tier, on the C ABI. Each folder has its own README. |
| `schema/` | The record schema and the fixtures every language must pass. |
| `docs/` | Architecture, the contract, versioning, releasing, supply chain, migration guides and the Kibana dashboard. Start at [`docs/README.md`](docs/README.md). |

## Build and test

```bash
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

The bindings are built and tested from their own folders; each README says how.

## Upgrading

- From Python 2.x: [`docs/migrating/python-2-to-3.md`](docs/migrating/python-2-to-3.md). The 2.0 API is kept; dispatch is now queued by
  default, the tamper chain is made at write time, a failing redaction plugin drops the record,
  `RedactPlugin` matches keys at every depth, and JSON lines are UTF-8.
- From JavaScript 1.x: [`docs/migrating/js-1-to-3.md`](docs/migrating/js-1-to-3.md).

## Documentation

- [`docs/README.md`](docs/README.md) — the documentation index.
- [`docs/architecture.md`](docs/architecture.md) — how the core, runtime and bindings fit together.
- [`docs/contract.md`](docs/contract.md) and [`docs/api-names.md`](docs/api-names.md) — what every language guarantees.
- [`docs/versioning.md`](docs/versioning.md), [`docs/supply-chain.md`](docs/supply-chain.md) and [`.github/SUPPORT.md`](.github/SUPPORT.md) — stability, security posture and support.
- [`docs/kibana/`](docs/kibana/) — a starter Kibana dashboard for ECS-mapped Elasticsearch logs.
- `CHANGELOG.md` — what changed; `CONTRIBUTING.md` — how to build and what a change must include.

## Support and security

Bugs and feature requests go to [GitHub issues](https://github.com/nikhilvdev/logquill/issues); see [SUPPORT](https://github.com/nikhilvdev/logquill/blob/main/.github/SUPPORT.md) for which versions
get fixes. Report vulnerabilities privately through [SECURITY](https://github.com/nikhilvdev/logquill/blob/main/.github/SECURITY.md), never as a public issue.
Contributions follow [CONTRIBUTING](https://github.com/nikhilvdev/logquill/blob/main/CONTRIBUTING.md) and the [Code of Conduct](https://github.com/nikhilvdev/logquill/blob/main/CODE_OF_CONDUCT.md).

## Licence

The 3.0 line is MIT licensed (see `LICENSE`). Python 2.0.0 stays FSL-1.1-MIT and every earlier release stays MIT, as published.
