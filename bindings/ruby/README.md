# logquill (Ruby)

Structured logging for Ruby on LogQuill's Rust core, through its C ABI (`crates/logquill-ffi`). The same record shape,
the same redaction and the same tamper-evident hash chain as the Python, Node, browser and other bindings: redaction and
the level filter run in Rust before anything is written.

> **Not published yet.** The gem is unreleased until the licence and release plan are settled
> (`docs/rust-core-plan.md`, section 8). See [Build and test](#build-and-test) to use it from this repository.

## Use it

```ruby
require "logquill"

log = Logquill::Logger.new(
  name: "web",
  plugins: [{ type: "redact" }, { type: "pii" }],
  transports: [{ type: "file", path: "web.log" }]
)

log.info("signed in", user: "alice@example.com", password: "hunter2")
log.child("db", component: "pg").warn("slow query", ms: 840)
log.close
```

`web.log` now has two JSON lines. In the first, `password` is `"***"` and the address in `user` is `***`; the second has
`"logger": "web.db"` and `component` and `ms` in `meta`. Call `close` (or `flush`) before the process ends: records are
written on a background thread.

### The logger

- `Logquill::Logger.new(name:, level:, plugins:, transports:, content_policy:, tamper_evident:, queue:, return_record:)`.
  `level` is `:trace :debug :info :warn :error :fatal` (default `:info`). A wrong option raises `Logquill::ConfigError`
  saying which one and what is allowed; an unknown option is an error, not ignored, so a typo cannot quietly leave a
  secret in the log.
- `log.info(message, key: value, ..., llm: { model:, tokens_in:, ... })`, and the same for `trace debug warn error fatal`.
  Returns `true` when the record was kept and `false` when it was filtered (or could not be logged). With
  `return_record: true` it returns the finished record as a Hash instead. **A log call never raises**: a value JSON cannot
  carry (NaN, a cycle, an object whose `to_s` raises, bytes that are not UTF-8) is turned into text, and a failure is
  reported with `Kernel#warn`.
- `log.child("db", component: "pg")` is a logger named `web.db` with that meta on every record, sharing the level and
  transports. `log.set_level(:error)` changes the level for the logger, its parent and its children.
- `plugins:` are `{ type: "redact", keys:, classes:, replacement: }`, `{ type: "pii", replacement: }`, `{ type: "sampling", rate: }`,
  `{ type: "rate_limit", max_records:, per_seconds:, key: }` and `{ type: "adaptive_sampling", ... }` (or build them with
  `Logquill::Plugins.redact`, `.pii`, `.sampling(rate:)`, `.rate_limit(...)`, `.adaptive_sampling(...)`), run in order in
  Rust. `content_policy:` is `off` (the default: prompt, completion and tool content is withheld), `hash`, `truncate` or
  `full`. `tamper_evident: true` chains each record as it is written; check a log file with `Logquill.verify_chain`.
- `transports:` takes Hashes for Rust transports (`{ type: "console", format: "json", colour: false }` or
  `{ type: "file", path:, format:, max_bytes:, backup_count:, compression: "gz" }`) and also any object that responds to
  `write(line, record)`: a Ruby transport, called on your thread with the finished, already-redacted record. It may also
  define `flush` and `close`.

### Context

```ruby
Logquill.with_context(request_id: "r-1") { log.info("handling") }   # request_id is in meta
```

The context is fiber-local: other threads do not see it, and a thread started inside the block does not inherit it. The
call's own fields win over the context.

### Helpers and the hash chain

Every stateless helper of the core is a method: `Logquill.canonical_json`, `pii_redact_text`, `redact_meta`,
`verify_chain`, `verify_signed_chain`, `format_text`, `parse_record`, `build_trace`, ... (`Logquill::HELPERS` lists them by
their Node-addon names; `Logquill.call("canonicalJson", ...)` runs one by that name). A record, `meta` or list of records
may be a Hash or a String of JSON text; **pass text when a number's spelling matters**, since `1` and `1.0` hash
differently in a chain. `Logquill::Chain` stamps records and signs its head:

```ruby
chain = Logquill::Chain.new
records = [chain.stamp(record_one), chain.stamp(record_two)]
Logquill.verify_chain(records)               # => { ok: true, records_checked: 2, head_hash: "..." }
Logquill.verify_signed_chain(records, "key", chain.sign_head("key"))   # also catches a deleted last line
```

## Not in this gem (yet)

The medium-tier bindings ship the facade, context, console / file transports and Ruby transports. Not here: HTTP, cloud and
database transports (write a Ruby transport), framework adapters and SDK patching, the standard-library `Logger` bridge,
and tail sampling, the flight recorder and the run summary (the C ABI does not carry them yet). Host-language *plugins* that change a record are not possible either: the Rust
transports have written it by the time Ruby sees it; use a Ruby transport to act on a record.

## Build and test

You need Ruby 3.1 or newer and a Rust toolchain. From the repository root:

```sh
cargo build -p logquill-ffi --release      # the shared library
cd bindings/ruby
gem install ffi minitest
ruby -Ilib test/test_logquill.rb           # 21 tests
ruby -Ilib test/test_readme.rb             # the example above is a file that is run, and this README must contain it
ruby scripts/pack_smoke.rb                 # build a platform gem, install it into an empty gem home, use it by name
```

The gem looks for the library in `LOGQUILL_LIB` (a path to the file), then inside the gem (`lib/logquill/native/`, how a
platform gem ships it), then in `target/release` and `target/debug` of this repository. The C ABI version is checked at
load: a library with a different one is refused.
