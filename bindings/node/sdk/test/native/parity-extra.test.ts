// The Python tests for the audit, metrics and graph areas that have a Node counterpart, ported
// (`docs/spec/legacy-tests/python-tests.txt`: test_audit, test_metrics_plugin, test_statsd_metrics_backend,
// test_prometheus_pushgateway_backend, graph/*). `features.test.ts` covers the main paths of the same code.
import { spawn, type ChildProcess } from "node:child_process";
import { createSocket } from "node:dgram";
import type { AddressInfo } from "node:net";
import { describe, expect, it } from "vitest";
import { AuditLogger } from "../../src/core/audit-logger.js";
import { verifyChain } from "../../src/core/integrity.js";
import { Level } from "../../src/core/levels.js";
import { Logger } from "../../src/core/logger.js";
import { nativeAddon } from "../../src/core/native.js";
import type { LogRecord } from "../../src/core/records.js";
import { MetricsPlugin, PrometheusPushgatewayBackend, StatsDBackend, type MetricsBackend } from "../../src/plugins/metrics-plugin.js";
import { FalkorDBTransport, Neo4jTransport, NeptuneTransport } from "../../src/transports/graph/backends.js";
import { CollectingTransport } from "../../src/transports/transport.js";

const core = Boolean(nativeAddon());
const runtime = Boolean(nativeAddon()?.StatsdBackend);
const settle = (ms = 30): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));
const rec = (level: string, message: string, meta: Record<string, unknown> = {}): LogRecord =>
  ({ schema_version: "2.0", timestamp: "t", level, logger: "app", message, meta }) as LogRecord;

describe.skipIf(!core)("AuditLogger (test_audit.py)", () => {
  const make = (options = {}) => {
    const sink = new CollectingTransport();
    return { sink, audit: new AuditLogger("audit", { transports: [sink], ...options }) };
  };

  it("a seeded secret and PII, and a secret inside a prompt, never reach the transport", async () => {
    const { audit, sink } = make();
    audit.info("signup", { password: "hunter2", note: "jo@example.com 123-45-6789" });
    audit.info("llm", { input_messages: [{ role: "user", content: "my key is sk-" + "a".repeat(30) }] });
    await audit.flush();
    const everything = JSON.stringify(sink.records);
    for (const seeded of ["hunter2", "jo@example.com", "123-45-6789", "sk-aaaa"]) expect(everything).not.toContain(seeded);
  });

  it("a child redacts too and continues the parent's hash chain", async () => {
    const { audit, sink } = make();
    audit.info("parent");
    audit.child("sub").info("child", { token: "t0ken" });
    await audit.flush();
    const [parent, child] = sink.records;
    expect(JSON.stringify(child)).not.toContain("t0ken");
    expect(child!.meta.prev_hash).toBe(parent!.meta.hash);
    expect(verifyChain(sink.formatted).ok).toBe(true);
  });

  it("content policy defaults to off, can be overridden, and a child inherits it", async () => {
    const off = make();
    expect(off.audit.contentPolicy).toBe("off");
    expect(off.audit.child("c").contentPolicy).toBe("off");
    const hashed = make({ contentPolicy: "hash" });
    expect(hashed.audit.contentPolicy).toBe("hash");
    expect(hashed.audit.child("c").contentPolicy).toBe("hash");
    hashed.audit.info("p", { input_messages: "secret prompt" });
    await hashed.audit.flush();
    expect(String(hashed.sink.records[0]!.meta.input_messages)).toMatch(/^sha256:/);
  });

  it("an invalid content policy still raises", () => {
    expect(() => new AuditLogger("a", { contentPolicy: "loud" as never })).toThrow();
  });

  it("extra plugins run after the bundled ones, and options are forwarded to the Logger", async () => {
    const seen: unknown[] = [];
    const { audit, sink } = make({
      level: Level.WARN,
      extraPlugins: [{ beforeLog: (r: LogRecord) => (seen.push(r.meta.hash, r.meta.password), r) }],
    });
    audit.info("below the level, never seen");
    audit.warn("seen", { password: "p" });
    await audit.flush();
    expect(sink.records).toHaveLength(1);
    expect(typeof seen[0]).toBe("string"); // the chain plugin had already run
    expect(seen[1]).toBe("***"); // and so had redaction
  });

  it("head hash and sign head are exposed", async () => {
    const { audit, sink } = make();
    audit.info("a");
    await audit.flush();
    expect(audit.headHash).toBe(sink.records[0]!.meta.hash);
    expect(audit.signHead("key")).toMatch(/^[0-9a-f]{64}$/);
  });
});

describe.skipIf(!core)("MetricsPlugin (test_metrics_plugin.py)", () => {
  const backend = () => {
    const calls: unknown[][] = [];
    const b: MetricsBackend & { closed?: boolean; close?: () => void } = {
      increment: (name, tags) => void calls.push(["inc", name, tags]),
      observe: (name, value, tags) => void calls.push(["obs", name, value, tags]),
    };
    return { calls, b };
  };

  it("FATAL counts as an error; INFO and WARN do not by default; an error with a duration does both", () => {
    const { calls, b } = backend();
    const plugin = new MetricsPlugin(b);
    plugin.afterLog(rec("INFO", "i"));
    plugin.afterLog(rec("WARN", "w"));
    plugin.afterLog(rec("FATAL", "f"));
    plugin.afterLog(rec("ERROR", "e", { duration_ms: 5 }));
    expect(calls.map((c) => c[0])).toEqual(["inc", "inc", "obs"]);
  });

  it("a record with no duration_ms (or a non-number) does not feed the histogram", () => {
    const { calls, b } = backend();
    const plugin = new MetricsPlugin(b);
    plugin.afterLog(rec("INFO", "a"));
    plugin.afterLog(rec("INFO", "b", { duration_ms: "fast" }));
    expect(calls).toEqual([]);
  });

  it("errorAt and the metric names are configurable", () => {
    const { calls, b } = backend();
    const plugin = new MetricsPlugin(b, { errorAt: "WARN", errorMetric: "my.errors", durationMetric: "my.ms" });
    plugin.afterLog(rec("WARN", "w", { duration_ms: 2 }));
    expect(calls).toEqual([
      ["inc", "my.errors", { logger: "app" }],
      ["obs", "my.ms", 2, { logger: "app" }],
    ]);
  });

  it("a raising backend does not crash the caller", async () => {
    const logger = new Logger("app", {
      plugins: [new MetricsPlugin({ increment: () => { throw new Error("down"); }, observe: () => undefined })],
    });
    expect(() => logger.error("boom")).not.toThrow();
    await logger.flush();
    expect(logger.info("still logging")).not.toBeNull();
  });

  it("close() closes a backend that has close(), and is safe when it has none", () => {
    const { b } = backend();
    let closed = 0;
    b.close = () => void closed++;
    new MetricsPlugin(b).close();
    expect(closed).toBe(1);
    expect(() => new MetricsPlugin(backend().b).close()).not.toThrow();
  });
});

describe.skipIf(!runtime)("StatsD backend (test_statsd_metrics_backend.py)", () => {
  it("prefix, dogstatsd tags, histogram lines; a failed send is swallowed", async () => {
    const server = createSocket("udp4");
    const received: string[] = [];
    server.on("message", (m) => received.push(m.toString()));
    await new Promise<void>((resolve) => server.bind(0, "127.0.0.1", resolve));
    const backend = new StatsDBackend({ host: "127.0.0.1", port: (server.address() as AddressInfo).port, prefix: "svc" });
    backend.increment("hits", { a: "1", b: "2" });
    backend.observe("ms", 3, {});
    await settle(100);
    server.close();
    expect(received).toContain("svc.hits:1|c|#a:1,b:2");
    expect(received).toContain("svc.ms:3.0|h"); // the core spells a float with its fraction, as in Python
    expect(() => new StatsDBackend({ host: "127.0.0.1", port: 1 }).increment("x", {})).not.toThrow();
  });
});

/** A pushgateway in another process (the Rust backend's flush blocks this thread) that records every request. */
async function gateway(): Promise<{ url: string; requests: () => { method: string; url: string; body: string }[]; stop: () => void }> {
  const child: ChildProcess = spawn(
    process.execPath,
    [
      "-e",
      `const s = require("http").createServer((req, res) => {
        let body = ""; req.on("data", (c) => (body += c));
        req.on("end", () => { res.end(); console.log(JSON.stringify({ method: req.method, url: req.url, body })); });
      });
      s.listen(0, "127.0.0.1", () => console.log("PORT " + s.address().port));`,
    ],
    { stdio: ["ignore", "pipe", "inherit"] },
  );
  const lines: string[] = [];
  let buffer = "";
  child.stdout!.on("data", (chunk: Buffer) => {
    buffer += chunk.toString();
    const parts = buffer.split("\n");
    buffer = parts.pop() ?? "";
    lines.push(...parts);
  });
  for (let i = 0; i < 100 && !lines.some((l) => l.startsWith("PORT")); i++) await settle(20);
  const port = lines.find((l) => l.startsWith("PORT"))!.slice(5);
  return {
    url: `http://127.0.0.1:${port}`,
    requests: () => lines.filter((l) => l.startsWith("{")).map((l) => JSON.parse(l) as { method: string; url: string; body: string }),
    stop: () => void child.kill(),
  };
}

describe.skipIf(!runtime)("pushgateway backend (test_prometheus_pushgateway_backend.py)", () => {
  it("nothing is sent until flush; nothing accumulated sends nothing; totals are cumulative; tag sets are separate series", async () => {
    const gw = await gateway();
    const backend = new PrometheusPushgatewayBackend({ gatewayUrl: gw.url, jobName: "job" });
    backend.flush();
    await settle(100);
    expect(gw.requests()).toHaveLength(0); // nothing accumulated: no request
    backend.increment("hits", { logger: "a" });
    backend.increment("hits", { logger: "a" });
    backend.increment("hits", { logger: "b" });
    backend.observe("latency", 2, { logger: "a" });
    backend.observe("latency", 3, { logger: "a" });
    await settle(100);
    expect(gw.requests()).toHaveLength(0); // still nothing: not before flush
    backend.flush();
    for (let i = 0; i < 100 && gw.requests().length === 0; i++) await settle(20);
    const body = gw.requests()[0]!.body;
    expect(gw.requests()[0]).toMatchObject({ method: "PUT", url: "/metrics/job/job" });
    expect(body).toContain('hits{logger="a"} 2');
    expect(body).toContain('hits{logger="b"} 1');
    expect(body).toContain('latency_sum{logger="a"} 5');
    expect(body).toContain('latency_count{logger="a"} 2');
    gw.stop();
  });

  it("metric names are sanitised: invalid characters become underscores, a leading digit gets a prefix", async () => {
    const gw = await gateway();
    const backend = new PrometheusPushgatewayBackend({ gatewayUrl: gw.url });
    backend.increment("my.metric-name", {});
    backend.increment("9lives", {});
    backend.flush();
    for (let i = 0; i < 100 && gw.requests().length === 0; i++) await settle(20);
    const body = gw.requests()[0]!.body;
    expect(body).toContain("my_metric_name 1");
    expect(body).toContain("_9lives 1");
    gw.stop();
  });

  it("close() pushes once", async () => {
    const gw = await gateway();
    const backend = new PrometheusPushgatewayBackend({ gatewayUrl: gw.url });
    backend.increment("c", {});
    backend.close();
    await settle(150);
    expect(gw.requests()).toHaveLength(1);
    gw.stop();
  });
});

describe.skipIf(!core)("graph transports (test_base_graph_transport.py and the backends)", () => {
  type Row = { key: string; props: Record<string, unknown>; session_key: string | null; parent_key: string | null };
  const capture = () => {
    const batches: Row[][] = [];
    const transport = new NeptuneTransport({
      maxRecords: 100,
      run: (_q, parameters) => void batches.push((JSON.parse(parameters) as { rows: Row[] }).rows),
    });
    return { batches, transport, rows: () => batches.flat() };
  };

  it("session grouping: run_id is preferred over trace_id, trace_id is used alone, neither means no session", async () => {
    const { transport, rows } = capture();
    const logger = new Logger("g", { transports: [transport] });
    logger.info("both", { run_id: "r", trace_id: "t", span_id: "s1" });
    logger.info("trace only", { trace_id: "t", span_id: "s2" });
    logger.info("neither", { span_id: "s3" });
    await logger.flush();
    transport.flush();
    await settle();
    expect(rows().map((r) => r.session_key)).toEqual(["r", "t", null]);
  });

  it("a root span has no parent key; a child points at its parent; a span is keyed by its own id", async () => {
    const { transport, rows } = capture();
    const logger = new Logger("g", { transports: [transport] });
    logger.info("root", { span_id: "root" });
    logger.info("child", { span_id: "kid", parent_span_id: "root" });
    await logger.flush();
    transport.flush();
    await settle();
    expect(rows().map((r) => [r.key, r.parent_key])).toEqual([["root", null], ["kid", "root"]]);
  });

  it("buffering is batched, not one statement per record", async () => {
    const { transport, batches } = capture();
    const logger = new Logger("g", { transports: [transport] });
    for (let i = 0; i < 25; i++) logger.info("n", { span_id: `s${String(i)}` });
    await logger.flush();
    transport.flush();
    await settle();
    expect(batches).toHaveLength(1);
    expect(batches[0]).toHaveLength(25);
  });

  it("Neo4j and FalkorDB: an injected client is not closed; the database is forwarded; a missing package gives an install hint", async () => {
    const sessions: unknown[] = [];
    let driverClosed = false;
    const driver = {
      session: (config?: unknown) => (sessions.push(config), { run: () => Promise.resolve(), close: () => Promise.resolve() }),
      close: () => ((driverClosed = true), Promise.resolve()),
    };
    const withDb = new Neo4jTransport({ driver, database: "graph", maxRecords: 1 });
    const without = new Neo4jTransport({ driver, maxRecords: 1 });
    new Logger("g", { transports: [withDb] }).info("a", { span_id: "1" });
    new Logger("g", { transports: [without] }).info("b", { span_id: "2" });
    await settle();
    withDb.close();
    expect(sessions).toEqual([{ database: "graph" }, {}]);
    expect(driverClosed).toBe(false);

    const hint = async (transport: unknown) => {
      try {
        await (transport as { execute(q: string, p: { rows: unknown[] }): Promise<void> }).execute("q", { rows: [] });
        return "";
      } catch (error) {
        return (error as Error).message;
      }
    };
    expect(await hint(new Neo4jTransport({ uri: "bolt://x" }))).toMatch(/npm install neo4j-driver/);
    expect(await hint(new FalkorDBTransport())).toMatch(/npm install falkordb/);
  });

  it("FalkorDB selects its graph once, however many batches", async () => {
    let selected = 0;
    let queries = 0;
    const client = {
      selectGraph: () => (selected++, { query: () => (queries++, Promise.resolve()) }),
    };
    const transport = new FalkorDBTransport({ client, maxRecords: 1 });
    const logger = new Logger("g", { transports: [transport] });
    for (let i = 0; i < 4; i++) logger.info("n", { span_id: `s${String(i)}` });
    await settle(80);
    expect(queries).toBe(4);
    expect(selected).toBe(1);
  });
});
