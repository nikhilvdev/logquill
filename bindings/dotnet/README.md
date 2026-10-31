# LogQuill for .NET

Structured logging for .NET on LogQuill's Rust core, through its C ABI (`crates/logquill-ffi`). The same record shape, the
same redaction and the same tamper-evident hash chain as the Python, Node, browser and other bindings: redaction and the
level filter run in Rust before anything is written.

> **Not published yet.** The package is unreleased: the 3.0 line is MIT licensed and a release candidate (3.0.0-rc.2) is prepared but not published
> (`docs/stages/stage-9.md`). See [Build and test](#build-and-test) to use it from this repository.

## Use it

```csharp
using LogQuill;

using var log = new Logger(new LoggerOptions
{
    Name = "web",
    Plugins = { PluginConfig.Redact(), PluginConfig.Pii() },
    Transports = { TransportConfig.File("web.log") },
});

log.Info("signed in", new { User = "alice@example.com", Password = "hunter2" });
log.Child("db", new { Component = "pg" }).Warn("slow query", new { Ms = 840 });
log.Close();
```

`web.log` now has two JSON lines. In the first, `password` is `"***"` and the address in `user` is `***`; the second has
`"logger": "web.db"` and `component` and `ms` in `meta`. Property names become `snake_case` keys, as the record contract
spells them. Call `Close()` (or `Flush()`) before the process ends: records are written on a background thread.

### The logger

- `new Logger(LoggerOptions)`: `Name`, `Level` (default `Info`), `Plugins`, `Transports`, `HostTransports`,
  `ContentPolicy`, `TamperEvident`, `Queue`. A wrong option throws `LogQuillConfigException` saying which one and what is
  allowed; an unknown option is an error, not ignored, so a typo cannot quietly leave a secret in the log.
- `log.Info(message, meta, llm)` and the same for `Trace Debug Warn Error Fatal`. `meta` is an anonymous object or a
  dictionary; `llm` is an LLM-call block (`new { Model = "...", TokensIn = 3 }`). Returns `true` when the record was kept
  and `false` when it was filtered. `LogRecord(...)` returns the finished record as JSON text instead (null if filtered).
  **A log call never throws**: NaN, a cycle, a property whose getter throws or text with a lone surrogate is turned into
  text, and a failure is written to standard error.
- `log.Child("db", new { Component = "pg" })` is a logger named `web.db` with that meta on every record, sharing the level
  and transports. `log.SetLevel(Level.Error)` changes it for the logger, its parent and its children.
- `PluginConfig.Redact(keys, classes, replacement)`, `Pii(replacement)`, `Sampling(rate)`, `RateLimit(maxRecords, perSeconds, key)` and
  `AdaptiveSampling(...)` run in Rust, in order.
  `ContentPolicy` is `off` (the default: prompt, completion and tool content is withheld), `hash`, `truncate` or `full`.
  `TamperEvident = true` chains each record as it is written; check a log file with `Core.VerifyChain`.
- `Transports` (`TransportConfig.Console(...)`, `TransportConfig.File(path, ...)`) are written by Rust on a background
  thread. `HostTransports` takes any `ITransport`: `Write(string line, JsonElement record)` is called on your thread with the
  finished, already-redacted record; `Flush()` and `Close()` are optional.
- A `Logger` is thread-safe and `IDisposable`; one that is never disposed is freed by its finalizer.

### Context

```csharp
using (LogContext.Push(new { RequestId = "r-1" })) { log.Info("handling"); }   // request_id is in meta
```

The context is an `AsyncLocal`: it is there after an `await` and in tasks started inside the scope, and not on unrelated
threads. The call's own fields win over the context.

### Helpers and the hash chain

`Core.Call("canonicalJson", ...)` runs any stateless helper of the core by the name the Node addon uses; typed methods
cover the common ones: `Core.CanonicalJson`, `PiiRedactText`, `RedactMeta`, `VerifyChain`, `VerifySignedChain`,
`SignHead`, `VerifyHeadSignature`. A record, meta or list of records may be an object or a `string` of JSON text;
**pass text when a number's spelling matters**, since `1` and `1.0` hash differently in a chain. `Chain` stamps records and
signs its head:

```csharp
using var chain = new Chain();
var records = new[] { chain.Stamp(recordOne), chain.Stamp(recordTwo) };
Core.VerifyChain("[" + string.Join(",", records) + "]");                    // Ok = true, RecordsChecked = 2
Core.VerifySignedChain(json, key, chain.SignHead(key));                      // also catches a deleted last line
```

## Not in this package (yet)

The medium-tier bindings ship the facade, context, console / file transports and .NET transports. Not here: HTTP, cloud and
database transports (write an `ITransport`), `Microsoft.Extensions.Logging` integration, framework adapters, and tail sampling, the flight recorder and the run summary (the C ABI does not carry them yet). Host-language *plugins* that change a record are not possible either: the Rust
transports have written it by the time .NET sees it; use an `ITransport` to act on a record.

## Build and test

You need the .NET 8 SDK or newer and a Rust toolchain. From the repository root:

```sh
cargo build -p logquill-ffi --release                  # the shared library
dotnet test bindings/dotnet/tests/LogQuill.Tests        # the example above is run for real by these tests
bindings/dotnet/scripts/pack-smoke.sh                   # pack a NuGet package, install it into a new app, run it
```

The library targets `net8.0` and `net10.0`; the tests run on the newest runtime installed. It looks for the native library
in `LOGQUILL_LIB` (a path to the file), then where the runtime looks for native assets (`runtimes/<rid>/native/` of the NuGet
package, the application directory), then in `target/release` and `target/debug` of this repository. The C ABI version is
checked at load: a library with a different one is refused.
