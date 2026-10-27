# logquill for Node

The Node binding of LogQuill's Rust core: a [napi-rs](https://napi.rs) addon (`src/`) and the TypeScript SDK on top
of it (`sdk/`). It is the 3.0 line; the status of each part is in `docs/stages/stage-6.md`.

```bash
npm install            # in bindings/node
npm run build          # the addon: index.js, index.d.ts, logquill.<platform>.node
npm run build:sdk      # the SDK: dist/ as ESM and CommonJS
npm test               # the addon and the shared corpus (node:test)
npm run test:sdk       # the SDK (vitest)
npm run smoke          # load dist/ as an ESM and a CommonJS consumer would
npm run build:wasm     # the WebAssembly fallback (needs `rustup target add wasm32-wasip1-threads`)
npm run pack-smoke     # pack the package, install the tarballs into a clean project, import by name
```

Node 22 or later (the lossless fixture reader needs `JSON.parse` source text, and Vitest 5 needs a current Node).

## Using it

```ts
import { Logger, FileTransport } from "logquill";

const log = new Logger("api", {
  transports: [new FileTransport("app.log", { maxBytes: 10_000_000, compression: "gz" })],
  meta: { service: "api" },
});
log.info("signed up", { user_id: 42 });
await log.close();
```

`FileTransport` runs on the Rust core: rotation, gzip and Fernet encryption happen on a worker thread, and a logger
whose transports are all native hands each record straight to it. A process that ends without calling `close()` is
drained on `exit` (at most five seconds); a hard kill is the application's to handle (`installShutdownHandlers`).

### What the Rust core adds over the 2.0 JavaScript package

```ts
import {
  AdaptiveSamplingPlugin, FlightRecorderPlugin, RunSummaryPlugin, MetricsPlugin,
  StatsDBackend, AuditLogger, verifySignedChain,
} from "logquill";

const transports = [new FileTransport("agent.log")];
const log = new Logger("agent", {
  transports,
  plugins: [
    new AdaptiveSamplingPlugin({ baseRate: 0.1, maxBytesPerSecond: 100_000 }), // keeps errors and slow spans
    new FlightRecorderPlugin({ transports }), // a failing run ships its DEBUG records, a passing one none
    new RunSummaryPlugin({ transports }), // one record per run: tokens, cost, tool calls, retries, errors
    new MetricsPlugin(new StatsDBackend({ host: "localhost" })),
  ],
});

// an audit trail: content off, secrets and PII redacted, every record hash-chained
const audit = new AuditLogger("audit", { transports });
const signature = audit.signHead(process.env.AUDIT_KEY!); // keep this where the writer cannot edit it
verifySignedChain(lines, process.env.AUDIT_KEY!, signature).ok; // false if a line was edited or the end cut off
```

`Neo4jTransport`, `FalkorDBTransport` and `NeptuneTransport` write the span tree as a graph, one `UNWIND` statement per
batch; pass a client (or, for Neo4j and FalkorDB, let them connect with the optional npm package). The graph
statement, row mapping, redaction, PII patterns, ECS, SQL rows and the GenAI attribute mapping come from the same Rust
code as every other binding, and the shared corpus (`schema/corpus/`, 22 groups) runs against the addon in
`tests/corpus.test.mjs`.

## How it differs from the 2.0 JavaScript package

- Records cross into Rust as JSON text. A JavaScript number cannot tell `1` from `1.0`, so the pure helpers
  (`canonicalJson`, `verifyChain`, the formatters) take JSON text; verify a log from its lines, not from parsed objects.
- A logger with only native transports skips the SDK's queue. For 100k records that is about 0.24 s end to end instead
  of about 4.1 s, at the cost of about 1.7 µs per call on the calling thread instead of 0.5 µs (the rest is deferred
  work in the old design). `afterLog` then fires once a record is handed to Rust, not once it is written.
- Transports written in JavaScript (console, HTTP, the cloud, SQL, NoSQL and queue sinks) stay on the SDK's own queue:
  the Rust worker cannot call back into JavaScript while an `exit` handler drains it. A logger with both kinds writes
  them in no fixed order.
- The addon is optional, in two steps. With no native binary for the platform the loader falls back to a
  WebAssembly build of the core (`npm run build:wasm`): the same redaction, chains, sampling, flight recorder,
  run summary and row mapping, but **no worker, file, HTTP or metrics sinks**. With no addon at all
  (`LOGQUILL_NO_NATIVE=1`) `FileTransport` and the redaction plugins use their JavaScript implementations, which
  agree with the shared corpus; the features that exist only in the core throw an error saying what is missing.

### Adapters and sinks

```ts
import { addTraceProcessor } from "@openai/agents";
import { Settings } from "llamaindex";
import { OpenAIAgentsAdapter, LlamaIndexAdapter, RustSQLiteTransport, OTelMetricsBackend, MetricsPlugin } from "logquill";

addTraceProcessor(new OpenAIAgentsAdapter(log));                  // an Agents SDK run becomes a span tree with usage
new LlamaIndexAdapter(log).attach(Settings.callbackManager);     // LlamaIndex.TS LLM and tool events
new Logger("db", { transports: [new RustSQLiteTransport({ filename: "logs.db", ensureSchema: true })] }); // written from Rust
new MetricsPlugin(new OTelMetricsBackend());                      // counters and histograms through the OpenTelemetry API
```

The adapters are duck-typed against the frameworks' published type declarations and import neither. The `Rust…Transport`
classes (syslog, SQLite, Datadog, New Relic, Elasticsearch, Application Insights, OTLP traces and logs) are alternatives to
the JavaScript transports of similar names; they need the native runtime (not the WebAssembly build).

### Alerts and level watching in Rust

`RustSlackAlertPlugin`, `RustPagerDutyAlertPlugin` and `RustEmailAlertPlugin` send from the Rust runtime (the same
de-duplication window and count as `AlertingPlugin`), so a slow webhook never touches the event loop. `RustLevelWatcher`
changes a logger's level when a file changes or an environment variable does (exactly one of the two):

```js
import { Logger, RustSlackAlertPlugin, RustLevelWatcher } from "logquill";
const log = new Logger("app").use(new RustSlackAlertPlugin({ webhookUrl: process.env.SLACK_WEBHOOK_URL }));
const watcher = new RustLevelWatcher(log, { file: "/etc/app/level" }).start(); // watcher.stop() when done
```

These need the native addon; without it use `SlackAlertPlugin` and friends.

## Not available in Node, and why

- **`diagnose` (local variables in tracebacks).** JavaScript cannot read another frame's locals. `error.cause` and the
  stack are captured as before; `KeyRedactor.redactLocal` is available for a host that has locals to show.
- **`AppriseAlertPlugin`.** Apprise is a Python library. Use `SlackAlertPlugin`, `PagerDutyAlertPlugin`,
  `EmailAlertPlugin`, or a custom `AlertingPlugin`.
- **CrewAI, AutoGen and LiteLLM adapters.** Python-only frameworks.
