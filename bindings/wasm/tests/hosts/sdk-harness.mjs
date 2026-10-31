// Checks of the browser SDK (dist/browser.mjs, the Node SDK's browser entry built on the WASM core), written for any
// JavaScript host like harness.mjs: no Node built-ins, no test framework. A host loads the bundle, passes it here with
// the place its .wasm lives, and gets `{total, passed, failures}` back. The order matters: the first checks run
// before `initWasm()` (the pure-JavaScript paths), the rest after it (the same plugins on the Rust core).

function same(a, b) {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a);
  return ka.length === Object.keys(b).length && ka.every((k) => Object.hasOwn(b, k) && same(a[k], b[k]));
}
const expect = (got, want) => {
  if (!same(got, want)) throw new Error(`expected ${JSON.stringify(want)}\n     got ${JSON.stringify(got)}`);
};
const rec = (level, message, meta = {}) => ({
  schema_version: "2.0",
  timestamp: "2026-01-01T00:00:00.000Z",
  level,
  logger: "app",
  message,
  meta,
});

/** `sdk` is the bundle's namespace; `source` is what `initWasm` is given (a URL, bytes or a `WebAssembly.Module`). */
export async function runSdk(sdk, source) {
  const failures = [];
  let total = 0;
  const check = async (name, fn) => {
    total += 1;
    try {
      await fn();
    } catch (error) {
      failures.push({ name: `sdk: ${name}`, error: String(error?.message ?? error) });
    }
  };
  const { Logger, CollectingTransport, ConsoleTransport, BeaconTransport, RedactPlugin, PIIRedactPlugin } = sdk;
  const logged = async (plugins, meta, level = "info") => {
    const sink = new CollectingTransport();
    const logger = new Logger("app", { transports: [sink], plugins });
    logger[level]("m", meta);
    await logger.flush();
    return sink.records[0]?.meta;
  };

  // ---- before the core is loaded: pure JavaScript everywhere -----------------------------------------------
  await check("the core is not ready before initWasm()", () => expect(sdk.wasmReady(), false));
  await check("before initWasm(), RedactPlugin redacts in JavaScript", async () => {
    const plugin = new RedactPlugin();
    expect(plugin.native === undefined, true);
    expect(await logged([plugin], { password: "hunter2", note: "ok" }), { password: "***", note: "ok" });
  });
  await check("before initWasm(), a Rust-only plugin refuses with a message that says what to do", () => {
    let message = "";
    try {
      new sdk.AdaptiveSamplingPlugin();
    } catch (error) {
      message = String(error?.message);
    }
    if (!/initWasm/.test(message)) throw new Error(`message was: ${message}`);
  });

  // ---- the SDK itself (does not depend on the core) --------------------------------------------------------
  await check("levels filter and a child logger inherits transports and fixed context", async () => {
    const sink = new CollectingTransport();
    const logger = new Logger("web", { level: "WARN", transports: [sink] });
    logger.info("dropped");
    logger.child("ui", { page: "home" }).error("kept");
    await logger.flush();
    expect(sink.records.map((r) => [r.level, r.logger, r.meta.page]), [["ERROR", "web.ui", "home"]]);
  });
  await check("thought / action / observation / decision set meta.kind", async () => {
    const sink = new CollectingTransport();
    const logger = new Logger("agent", { transports: [sink] });
    logger.thought("t");
    logger.action("a");
    logger.observation("o");
    logger.decision("d");
    await logger.flush();
    expect(sink.records.map((r) => r.meta.kind), ["thought", "action", "observation", "decision"]);
  });
  await check("nested spans link by parent_span_id and report a duration (a plain stack, not async-local storage)", async () => {
    const sink = new CollectingTransport();
    const logger = new Logger("web", { transports: [sink] });
    await logger.span("outer", async () => {
      await logger.span("inner", async () => {});
    });
    await logger.flush();
    const [inner, outer] = sink.records; // inner closes first
    expect([inner.message, outer.message], ["inner", "outer"]);
    expect(inner.meta.parent_span_id, outer.meta.span_id);
    if (typeof inner.meta.duration_ms !== "number") throw new Error("no duration_ms");
  });
  await check("a span whose block throws still logs, and rethrows", async () => {
    const sink = new CollectingTransport();
    const logger = new Logger("web", { transports: [sink] });
    let caught;
    try {
      await logger.span("boom", () => {
        throw new Error("nope");
      });
    } catch (error) {
      caught = error;
    }
    await logger.flush();
    expect([caught?.message, sink.records.length], ["nope", 1]);
  });
  await check("ConsoleTransport sends ERROR to console.error and the rest to console.log", async () => {
    const seen = [];
    const fake = { log: (m) => seen.push(["log", m]), error: (m) => seen.push(["error", m]) };
    const logger = new Logger("web", { transports: [new ConsoleTransport({ console: fake, colorize: false })] });
    logger.info("fine");
    logger.error("bad");
    await logger.flush();
    expect(seen.map(([stream]) => stream), ["log", "error"]);
  });
  await check("BeaconTransport sends a full batch as it fills, the rest on flush, newline-joined", async () => {
    const sent = [];
    const beacon = new BeaconTransport("https://logs.example/ingest", { batchSize: 3, sender: (url, batch) => sent.push([url, [...batch]]) });
    const logger = new Logger("web", { transports: [beacon] });
    for (let i = 0; i < 4; i++) logger.info(`m${i}`);
    await logger.close(); // flush() only hands records over; close() makes a batching transport send what it holds
    expect(sent.map(([url, batch]) => [url, batch.length]), [["https://logs.example/ingest", 3], ["https://logs.example/ingest", 1]]);
  });

  // ---- load the core -----------------------------------------------------------------------------------------
  await check("initWasm() loads the core, and calling it again is harmless", async () => {
    await sdk.initWasm(source);
    await sdk.initWasm(source);
    expect(sdk.wasmReady(), true);
  });

  // ---- after: the same plugins, now on the Rust core ---------------------------------------------------------
  await check("after initWasm(), RedactPlugin and PIIRedactPlugin run on the core", () => {
    expect([new RedactPlugin().native !== undefined, new PIIRedactPlugin().native !== undefined], [true, true]);
  });
  await check("the core and the JavaScript path give the same redaction", async () => {
    const meta = { password: "hunter2", Authorization: "Bearer x", nested: { token: "t", keep: 1 }, list: [{ secret: "s" }] };
    expect(await logged([new RedactPlugin()], meta), {
      password: "***",
      Authorization: "***",
      nested: { token: "***", keep: 1 },
      list: [{ secret: "***" }],
    });
  });
  await check("PIIRedactPlugin masks emails and SSNs inside strings, at any depth", async () => {
    const out = await logged([new PIIRedactPlugin()], { note: "mail alice@example.com re 123-45-6789", deep: { x: ["bob@example.org"] } });
    if (/alice@example\.com|123-45-6789|bob@example\.org/.test(JSON.stringify(out))) throw new Error(JSON.stringify(out));
  });
  await check("AdaptiveSamplingPlugin keeps errors even when it samples everything else out", () => {
    const plugin = new sdk.AdaptiveSamplingPlugin({ baseRate: 0, minRate: 0, maxRate: 0 });
    expect([plugin.beforeLog(rec("ERROR", "boom")) !== null, plugin.beforeLog(rec("INFO", "chatter")) === null], [true, true]);
  });
  await check("FlightRecorderPlugin holds back low-level records and ships them when the run errors", () => {
    const shipped = new CollectingTransport();
    const plugin = new sdk.FlightRecorderPlugin({ shipAt: "INFO", flushAt: "ERROR", transports: [shipped] });
    const held = plugin.beforeLog(rec("DEBUG", "step 1", { run_id: "r1" }));
    expect([held, plugin.bufferedRecords], [null, 1]);
    plugin.beforeLog(rec("ERROR", "failed", { run_id: "r1" }));
    expect(shipped.records.map((r) => r.message), ["step 1"]);
  });
  await check("RunSummaryPlugin writes one summary record when a run's root span closes", async () => {
    const sink = new CollectingTransport();
    const transports = [sink];
    const logger = new Logger("app", { transports, plugins: [new sdk.RunSummaryPlugin({ transports })] });
    await logger.span("run", async () => {
      logger.info("working", { run_id: "r1" });
    }, { run_id: "r1" });
    await logger.flush();
    const summaries = sink.records.filter((r) => r.logger === "app.run_summary");
    expect(summaries.length, 1);
  });

  return { total, passed: total - failures.length, failures };
}
