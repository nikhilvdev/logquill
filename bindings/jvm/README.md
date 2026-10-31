# LogQuill for the JVM

Structured logging for Java (and Kotlin, Scala, ...) on LogQuill's Rust core, through its C ABI (`crates/logquill-ffi`) loaded
with JNA. The same record shape, the same redaction and the same tamper-evident hash chain as the Python, Node, browser and
other bindings: redaction and the level filter run in Rust before anything is written.

> **Not published yet.** The library is unreleased: the 3.0 line is MIT licensed and a release candidate (3.0.0-rc.2) is prepared but not published
> (`docs/stages/stage-9.md`), and the Maven group ID is `io.github.nikhilvdev` (the Java package stays `io.logquill`), to be verified on Central before the first publish. See
> [Build and test](#build-and-test) to use it from this repository.

## Use it

```java
package examples;

import io.logquill.Logger;
import io.logquill.LoggerOptions;
import io.logquill.Plugins;
import io.logquill.Transports;
import java.util.Map;

public class Quickstart {
    public static void main(String[] args) {
        String path = args.length > 0 ? args[0] : "web.log";
        LoggerOptions options = LoggerOptions.builder()
                .name("web")
                .plugin(Plugins.redact())
                .plugin(Plugins.pii())
                .transport(Transports.file(path))
                .build();
        try (Logger log = new Logger(options)) {
            log.info("signed in", Map.of("user", "alice@example.com", "password", "hunter2"));
            log.child("db", Map.of("component", "pg")).warn("slow query", Map.of("ms", 840));
        }
    }
}
```

`web.log` now has two JSON lines. In the first, `password` is `"***"` and the address in `user` is `***`; the second has
`"logger": "web.db"` and `component` and `ms` in `meta`. Close the logger (try-with-resources) before the process ends:
records are written on a background thread, and `close()` is what waits for them.

### The logger

- `new Logger(LoggerOptions)`; build options with `LoggerOptions.builder()`: `name`, `level` (default `INFO`), `plugin`,
  `transport`, `hostTransport`, `contentPolicy`, `tamperEvident`, `queue`. A wrong option throws
  `LogQuillException.Config` saying which one and what is allowed; an unknown option is an error, not ignored, so a typo cannot
  quietly leave a secret in the log.
- `log.info(message, meta)` and the same for `trace debug warn error fatal` (and `log.info(message)` with no meta). `meta` is a
  `Map` or any bean (properties become `snake_case` keys). They return `true` when the record was kept and `false` when it was
  filtered. `log.log(level, message, meta, llm)` is the general call; `llm` is an LLM-call block (`model`, `tokens_in`, ...).
  `logRecord(...)` returns the finished record as JSON text instead (null if filtered). **A log call never throws**: NaN, a
  cycle, a bean whose getter throws or an `Instant` is turned into JSON or text, and a failure is written to standard error.
- `log.child("db", Map.of("component", "pg"))` is a logger named `web.db` with that meta on every record, sharing the level and
  transports. `log.setLevel(Level.ERROR)` changes it for the logger, its parent and its children.
- `Plugins.redact()` / `Plugins.redact(keys, classes, replacement)`, `Plugins.pii()`, `Plugins.sampling(rate)`,
  `Plugins.rateLimit(maxRecords, perSeconds, key)` and `Plugins.adaptiveSampling(options)` run in Rust, in order.
  `contentPolicy` is `off` (the default: prompt, completion and tool content is withheld), `hash`, `truncate` or `full`.
  `tamperEvident(true)` chains each record as it is written; check a log file with `Core.verifyChain`.
- `Transports.console(...)` and `Transports.file(path, ...)` are written by Rust on a background thread. A `HostTransport` is
  `write(String line, Map<String,Object> record)` (plus optional `flush` and `close`), called on your thread with the finished,
  already-redacted record; an exception in one is reported and does not stop the others.
- A `Logger` is thread-safe. One that is never closed is freed by the garbage collector, but only `close()` flushes the queue.

### Context

```java
LogContext.with(Map.of("request_id", "r-1"), () -> log.info("handling"));   // request_id is in meta
```

The context is thread-local: other threads do not see it, and a thread started inside the block does not inherit it. The
call's own fields win over the context.

### Helpers and the hash chain

`Core.call("canonicalJson", ...)` runs any stateless helper of the core by the name the Node addon uses; typed methods cover
the common ones: `Core.canonicalJson`, `piiRedactText`, `redactMeta`, `verifyChain`, `verifySignedChain`, `signHead`,
`verifyHeadSignature`. A record, meta or list of records may be an object or a `String` of JSON text; **pass text when a
number's spelling matters**, since `1` and `1.0` hash differently in a chain. `Chain` stamps records and signs its head:

```java
try (Chain chain = new Chain()) {
    List<String> records = List.of(chain.stamp(recordOne), chain.stamp(recordTwo));
    Core.verifyChain("[" + String.join(",", records) + "]", null);       // ok() == true, recordsChecked() == 2
    Core.verifySignedChain(json, key, chain.signHead(key), null);         // also catches a deleted last line
}
```

## Not in this library (yet)

The medium-tier bindings ship the facade, context, console / file transports and Java transports. Not here: HTTP, cloud and
database transports (write a `HostTransport`), SLF4J / Log4j / `java.util.logging` bridges, Kotlin extensions, framework
adapters, and tail sampling, the flight recorder and the run summary (the C ABI does not carry them yet). Host-language *plugins* that change a record are not possible either: the Rust
transports have written it by the time Java sees it; use a `HostTransport` to act on a record.

## Build and test

You need JDK 17 or newer and a Rust toolchain. From the repository root:

```sh
cargo build -p logquill-ffi --release          # the shared library
cd bindings/jvm
gradle test                                     # 19 tests, including the example above run for real
scripts/pack-smoke.sh                           # publish the jar to a local Maven repo, consume it from a fresh Gradle app
```

The library finds the native library in `LOGQUILL_LIB` (a path to the file), then through JNA (a jar carrying it under
`<os>-<arch>/`, such as `darwin-aarch64/liblogquill_ffi.dylib`, or `jna.library.path`), then in `target/release` and
`target/debug` of this repository. The C ABI version is checked at load: a library with a different one is refused.
