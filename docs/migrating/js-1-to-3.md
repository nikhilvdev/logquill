# Migrating from logquill 1.x to 3.0 (Node)

The JavaScript package never released a 2.x line, so 3.0 is the first release after 1.x and carries both steps: the record
and contract changes that 2.0 made (the same ones the Python package made in its 2.0), and the move to the Rust core. This
guide lists both. Application code that only calls `logger.info(...)` and friends needs no change; what changes is what is on
disk and on the wire, the Node version, and how the package is installed.

> **Not released yet.** The 3.0 Node package (`bindings/node`) is built from the `develop` branch, is MIT licensed, and
> nothing is on npm yet. The platform packages for targets other than the build machine's have not been built. The 1.x package stays as it is in the `logquill-js` repository.

## 1. Install and runtime

- **Node 22 or later** (`engines.node` is `>=22`). Node 20 is unsupported.
- The package is a compiled addon (napi-rs) for macOS (arm64, x64), Linux (gnu x64 and arm64, musl x64) and Windows x64, with
  a **WebAssembly fallback** for any other platform and for browsers. The SDK is ESM and CommonJS (`dist/`).
- Until a release, build it from a checkout: `npm install`, `npm run build`, `npm run build:sdk` in `bindings/node`.

## 2. Records and contract (1.x to the shared 2.0 contract)

These are the changes of `docs/legacy/js/MIGRATING.md`, summarised; read it for the examples and SQL.

1. **Every record carries `"schema_version": "2.0"`.** A parser that accepts "exactly these five top-level keys" breaks. Old
   1.x lines need no conversion: a record with no `schema_version` is read as `"1.0"` by `parseRecord`.
2. **Named `meta` keys are snake_case:** `runId`→`run_id`, `spanId`→`span_id`, `parentSpanId`→`parent_span_id`,
   `traceId`→`trace_id`, `durationMs`→`duration_ms`, `prevHash`→`prev_hash`. Your own keys are untouched, and the options
   you pass in code (`RunPlugin({ runId })`, `span(..., { parentSpanId })`) keep their camelCase names. Anything that reads or
   writes those keys in `meta` directly must change.
3. **SQL and DynamoDB columns** are `run_id`, `span_id`, `parent_span_id`, `trace_id`. The package does not alter your tables:
   rename the columns, or write to a new table. A DynamoDB partition key cannot be renamed; create a new table.
4. **Tamper-evident chains use Python's canonical JSON.** A chain written by 1.x does **not** verify under 3.0, because its
   hashes were computed over other bytes and key names. Keep a 1.x install to verify old files, or start the 3.0 chain from
   the last 1.x hash with `genesisHash`.
5. **Content fields are off by default.** `meta.input_messages`, `output_messages`, `system_instructions`, `tool_arguments`,
   `tool_result` and `state_diff` are replaced by a placeholder before any plugin or transport sees them. To keep them, pass
   `contentPolicy: "full"` (or `"hash"`, `"truncate"`) to `Logger`; children inherit it.

## 3. What 3.0 changes on top

- **Native transports skip the JavaScript queue.** A logger whose transports are all native hands records straight to Rust.
  Measured on the build machine: 100,000 records reach disk in about 0.24 s instead of about 4.1 s, at the price of about
  1.7 µs per call on the caller instead of 0.5 µs, because serialising and handing over happen inline.
- **`afterLog` fires when the record is handed to Rust, not when it is written,** for native transports.
- **Console and HTTP transports stay in JavaScript** (they write through `console` and `fetch`, which your code and tests
  replace). Transports you write in JavaScript stay on the SDK's own queue. A logger with both kinds writes them in no fixed
  order.
- **Exit.** A native dispatcher is drained synchronously on `process` `exit` (at most 5 seconds), including after
  `process.exit(3)`. A hard kill is still yours to handle (`installShutdownHandlers`).
- **A redaction plugin that fails drops the record (D20).** Set `failsClosed` on your own plugin class to get the same.
- **`NaN`, `Infinity` and `-Infinity` words are accepted in input text (D19).**
- **Hash chains and numbers.** The chain hashes the canonical text, so the number spelling matters: JavaScript cannot tell `1`
  from `1.0`. Where an integral float written by Python (`12.0`) is re-read by JavaScript it hashes as `12`; keep chain
  input as text when it comes from another language. The details are in the legacy guide, section 4.

## 4. Checklist

1. Upgrade Node to 22 or later; install the 3.0 package into a clean project.
2. Update every place that reads or writes the camelCase `meta` keys, and any SQL or DynamoDB schema.
3. Decide a `contentPolicy` for each logger that logs prompts or tool results.
4. Verify any existing 1.x chain with a 1.x install; start 3.0 chains with `genesisHash` if you need continuity.
5. If you depend on `afterLog` meaning "written", move that check to `flush()`.
