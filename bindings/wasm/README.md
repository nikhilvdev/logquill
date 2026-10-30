# @logquill/wasm

LogQuill for the browser, Deno, Bun and Cloudflare Workers: the Rust core compiled to WebAssembly, and a small SDK on
top of it. One record shape and one set of redaction, sampling and integrity rules, the same as the Python and Node
packages, in places where a native addon cannot load.

> **Not published yet.** The package is unreleased: the 3.0 line is MIT licensed and a release candidate (3.0.0-rc.1) is prepared but not published
> (`docs/stages/stage-9.md`). Build it from this repository as described under
> [Build and test](#build-and-test).

Use it when your code runs somewhere a native addon cannot: a web page, an edge function, Deno, Bun. On Node, install
`logquill` instead, which runs the same core natively and has the file, HTTP, SQL, cloud and queue transports.

## Use it

Load the module once with `initWasm()`, **then** build your loggers. Plugins choose Rust or JavaScript when they are
constructed, so a logger built before `initWasm()` finishes keeps its pure-JavaScript plugins (same output, slower).

### In a page

```html
<script type="module">
  import { initWasm, Logger, ConsoleTransport, RedactPlugin, PIIRedactPlugin } from "@logquill/wasm";

  await initWasm(); // fetches logquill_wasm_bg.wasm from beside the bundle

  const log = new Logger("web", {
    transports: [new ConsoleTransport({ colorize: false })],
    plugins: [new RedactPlugin(), new PIIRedactPlugin()],
  });
  log.info("signed in", { user: "alice@example.com", password: "hunter2" });
  await log.flush();
</script>
```

This prints one line with `"user":"***"` and `"password":"***"`: the key redactor masks `password`, and the PII
scanner masks the address inside `user`.

### In Deno or Bun

```js
import { initWasm, Logger, ConsoleTransport, RedactPlugin, PIIRedactPlugin } from "@logquill/wasm";

await initWasm(); // Deno and Bun can fetch a file: URL; on Node pass the bytes: initWasm(readFileSync(path))

const log = new Logger("server", {
  transports: [new ConsoleTransport({ colorize: false })],
  plugins: [new RedactPlugin(), new PIIRedactPlugin()],
});
log.info("signed in", { user: "alice@example.com", password: "hunter2" });
await log.flush();
```

Deno needs `--allow-read` to fetch the module from disk. On Node, read the file and pass the bytes
(`initWasm(readFileSync(...))`), since Node's `fetch` does not take `file:` URLs.

### In a Cloudflare Worker

```js
import wasm from "@logquill/wasm/logquill_wasm_bg.wasm";
import { initWasm, Logger, CollectingTransport, RedactPlugin } from "@logquill/wasm";

export default {
  async fetch() {
    await initWasm(wasm); // a Worker cannot fetch or compile WebAssembly, so it is imported precompiled

    const sink = new CollectingTransport();
    const log = new Logger("edge", { transports: [sink], plugins: [new RedactPlugin()] });
    log.info("request", { token: "abc123", path: "/" });
    await log.flush();

    return Response.json(sink.records[0].meta);
  },
};
```

Every example above is run by `npm run test:examples`, which also checks that this README contains the files in
`examples/` word for word.

## What is in it

| | |
|---|---|
| `Logger`, levels, child loggers, `.thought()` `.action()` `.observation()` `.decision()`, `opt()` | yes |
| JSON, text and logfmt formatters | yes |
| Bound context and `span()` | yes, on a plain stack (see below) |
| `ConsoleTransport`, `BeaconTransport` (`navigator.sendBeacon`, falling back to `fetch` with `keepalive`) | yes |
| `RedactPlugin`, `PIIRedactPlugin` | yes, on the Rust core once loaded |
| `SamplingPlugin`, `ContextPlugin` | yes, in JavaScript (they do not need the core) |
| `AdaptiveSamplingPlugin`, `FlightRecorderPlugin`, `RunSummaryPlugin` | yes, **only** on the Rust core |
| File, HTTP, SQL, NoSQL, queue and cloud transports; LangChain, winston and pino adapters; `logquill` CLI | no: use `logquill` on Node |

The raw core, with the Node addon's function names and the same JSON-text boundary, is at `@logquill/wasm/core`. It is a separate module instance from the SDK, so it has its own `init()` and
`initSync()` to call first:
`canonicalJson`, `formatJson`, `parseRecord`, `verifyChain`, `Chain`, `KeyRedactor`, `PiiRedactor`, `Sampling`,
`buildTrace`, and the rest of the core-only surface (`bindings/node/index.d.ts` lists them; the transports and the
worker are not in this build).

## Things to know

- **Spans are not isolated across concurrent awaits.** A browser has no `AsyncLocalStorage`, so nesting and bound
  context use a plain stack, popped when the block's promise settles. That is right for one open block at a time. Two
  `span()` blocks awaiting at once can pick up each other's parent. Pass `parentSpanId` explicitly where it matters.
- **Content Security Policy.** A page with a strict `script-src` must allow `'wasm-unsafe-eval'`, or the browser will not
  compile the module.
- **No background thread.** The Rust runtime (worker thread, files, sockets) is not part of this build. Records are
  handed to transports on a later turn of the event loop (a microtask in a page or a Worker), and `await log.flush()`
  waits for that. `BeaconTransport` sends a full batch as
  it fills; `await log.close()` makes it send what it holds, which is what to do on `pagehide`.
- **The clock is `Date.now()`.** `std::time::SystemTime` panics on `wasm32-unknown-unknown`, so the module is handed the
  time from JavaScript.
- **Not tested:** Firefox and Safari (only Chrome is driven). The SDK needs only WebAssembly; the test harness also
  needs `JSON.parse` source-text access (the Chrome, Deno, Bun and workerd versions used here all have it).

## Size and speed

| | |
|---|---|
| `logquill_wasm_bg.wasm` | 820 KiB, **319 KB gzipped** |
| SDK bundle (`dist/browser.mjs`) | 119 KiB, 26.6 KB gzipped |
| Cold compile in V8 | about 1 ms |
| `formatJson`, one record | 2.1 µs |
| `KeyRedactor.redactMeta`, a nested object | 2.5 µs |
| `PiiRedactor.redactMeta`, three strings | 4.6 µs |
| `PiiRedactor.redactText`, 4 KB of prose | 32 µs |

Measured on an M-series Mac with Node 26 (`npm run bench`); a phone is slower, and calling in from JavaScript adds a
JSON round trip that the numbers include. `budget.json` holds the limits CI enforces (`npm run check:budget`): size to
within about 8%, speed to 7 to 10 times these figures so that a slow runner does not fail the build, but losing the
regex engine's lazy DFA (28 to 55 times slower PII scanning) does.

The module was 457 KB gzipped before the Unicode tables the redaction patterns never read were left out and the
debug sections were stripped. `wasm-opt` was tried and left out: the version available from npm produced a file V8
rejects, and it saved nothing once gzipped.

## Build and test

You need Rust with the `wasm32-unknown-unknown` target, Node 22 or newer, and the **exact** `wasm-bindgen` CLI that
`Cargo.toml` pins (the build script checks it and says what to install):

```sh
rustup target add wasm32-unknown-unknown
cargo install wasm-bindgen-cli --version 0.2.129 --locked
npm ci --ignore-scripts && npm rebuild bun   # Bun's launcher needs its one install script
npm run build        # cargo + wasm-bindgen into pkg/
npm run build:sdk    # the SDK bundle and types into dist/
npm test             # everything below
```

`npm test` runs the whole shared corpus (913 checks) against the module in Node, then again, with 16 SDK checks, in
**Deno, Bun, headless Chrome and a Worker under workerd** (929 each), and then the size and speed budget. Run one host
with `npm run test:deno`, `test:bun`, `test:chrome` or `test:worker`. Chrome must be installed; `CHROME_PATH` selects
a binary. `npm run test:examples` runs the examples above.

The same corpus checks the native addon and the Python binding, so a result that differs between bindings fails there.
