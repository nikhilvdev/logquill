# LogQuill for Go

Structured logging for Go on LogQuill's Rust core: the same record shape, the same redaction and the same tamper-evident hash
chain as the Python, Node, browser and other bindings. The core runs as **WebAssembly inside your process** (wazero), so there
is no cgo, no shared library to ship and nothing to install besides the module. Redaction and the level filter run in the
core before anything is written.

> **Not published yet.** The module is unreleased until the licence and release plan are settled
> (`docs/rust-core-plan.md`, section 8). See [Build and test](#build-and-test) to use it from this repository.

## Use it

```go
package main

import (
	"os"

	logquill "github.com/nikhilvdev/logquill/bindings/go/v3"
)

func main() {
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

	db, _ := log.Child("db", logquill.Meta{"component": "pg"})
	defer db.Close()
	db.Warn("slow query", logquill.Meta{"ms": 840})
}
```

This prints two JSON lines. In the first, `password` is `"***"` and the address in `user` is `***`; the second has
`"logger": "web.db"` and `component` and `ms` in `meta`.

### The logger

- `logquill.New(opts...)`: `WithName`, `WithLevel` (default `Info`), `WithPlugins`, `WithTransports`, `WithHostPlugins`,
  `WithContentPolicy` and `WithTamperEvident`. A wrong option returns a `*ConfigError` saying which one and what is allowed;
  an unknown value is an error, not ignored, so a typo cannot quietly leave a secret in the log.
- `log.Info(message, logquill.Meta{...})` and the same for `Trace Debug Warn Error Fatal`. They return `true` when the record
  was kept and `false` when it was filtered. `log.Log(level, message, meta, llm)` is the general call; `llm` is an LLM-call block
  (`model`, `tokens_in`, `tokens_out`, `cost_usd`, ...). **A log call never panics or returns an error**: NaN, a cycle, a
  channel or invalid UTF-8 in `meta` is turned into text, and a failure is written to standard error.
- `log.Child("db", fixedMeta)` is a logger named `web.db` with that meta on every record, sharing the level, plugins and
  transports. `log.SetLevel(logquill.Error)` changes the level for the logger, its parent and its children.
- `Redact(keys, classes, replacement)` and `PII(replacement)` run in the core, in order. `WithContentPolicy` is `off` (the
  default: prompt, completion and tool content is withheld), `hash`, `truncate` or `full`. `WithTamperEvident()` chains each
  record as it is written; check a log with `logquill.VerifyChain`.
- A `Transport` is `Write(level Level, line []byte) error` (and optionally `Flush`/`Close`). `NewConsoleTransport`,
  `NewFileTransport` (appends; use `logrotate` and `Reopen`) and `NewRotatingFileTransport(path, maxBytes, backupCount)` (rotates by
  size to `path.1`, `path.2`, ...; no compression) are included. Records reach a
  transport one at a time, in chain order, after the host plugins.
- `Redact`, `PII`, `Sampling(rate)`, `RateLimit(maxRecords, perSeconds, key)` and `AdaptiveSampling(AdaptiveOptions)` are `Plugin`s that run
  in the core, in order.
- A `HostPlugin` is `func(record map[string]any) map[string]any`: it sees the record after the core's pipeline (after
  redaction), may change it, and returns `nil` to drop it. Numbers arrive as `json.Number`, keeping their spelling.
- Close a logger when done; a logger's children keep its resources alive until they are closed too. A `Logger` is safe for
  use by many goroutines.

### Helpers and the hash chain

`logquill.Call("canonicalJson", ...)` runs any stateless helper of the core by the name the Node addon uses; typed functions
cover the common ones: `CanonicalJSON`, `PIIRedactText`, `RedactMeta`, `VerifyChain`, `VerifySignedChain`, `SignHead`,
`VerifyHeadSignature`. A record, meta or list of records may be a Go value, a `json.RawMessage` or a `string` of JSON text;
**pass text when a number's spelling matters**, since `1` and `1.0` hash differently in a chain. `logquill.NewChain("")`
stamps records and signs its head.

## Cost

The core is WebAssembly, so a call copies its strings into the module's memory and the record back out. Measured on an
Apple M-series Mac (`go test -bench .`): a log call with redaction and PII scanning costs about **6 µs** (48 allocations), a
call the level filters out about **1.3 µs**, a stateless helper call about **1.6 µs**. Every `Logger` is its own WebAssembly
instance (its own memory, its own lock), so loggers do not wait on each other; calls on one logger are serialised.

## Not in this module (yet)

The medium-tier bindings ship the facade, console / file transports and host plugins. Not here: HTTP, cloud and database
transports (write a `Transport`), `log/slog` and `context.Context` integration, compression of rotated files, and tail sampling, the flight recorder and the run summary (the C ABI does not carry them yet).

## Build and test

You need Go 1.24 or newer and a Rust toolchain. From the repository root:

```sh
bindings/go/scripts/build-core.sh          # builds core.wasm (the core for wasm32-wasip1) next to the Go sources
cd bindings/go
go test ./...                              # includes running the example above and checking this README contains it
go test -race ./... && go test -run '^$' -bench .
scripts/consumer-smoke.sh                  # a fresh module imports this one by path and runs
```

`core.wasm` is embedded with `//go:embed`. The Go module proxy builds from a tagged tree, so a release must **commit**
`core.wasm` (it is untracked day to day); the release flow does that before tagging.
