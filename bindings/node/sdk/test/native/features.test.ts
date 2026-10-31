import { spawn } from "node:child_process";
import { createSocket } from "node:dgram";
import type { AddressInfo } from "node:net";
import { describe, expect, it } from "vitest";
import { AuditLogger } from "../../src/core/audit-logger.js";
import { verifyChain, verifyHeadSignature, verifySignedChain } from "../../src/core/integrity.js";
import { Level } from "../../src/core/levels.js";
import { Logger } from "../../src/core/logger.js";
import { nativeAddon } from "../../src/core/native.js";
import type { LogRecord } from "../../src/core/records.js";
import {
  AdaptiveSamplingPlugin,
  FlightRecorderPlugin,
  RunSummaryPlugin,
} from "../../src/plugins/native-plugins.js";
import {
  MetricsPlugin,
  PrometheusPushgatewayBackend,
  StatsDBackend,
  type MetricsBackend,
} from "../../src/plugins/metrics-plugin.js";
import {
  FalkorDBTransport,
  Neo4jTransport,
  NeptuneTransport,
} from "../../src/transports/graph/backends.js";
import { CollectingTransport } from "../../src/transports/transport.js";

const record = (level: string, message: string, meta: Record<string, unknown> = {}): LogRecord =>
  ({
    schema_version: "2.0",
    timestamp: "2026-01-01T00:00:00.000Z",
    level,
    logger: "app",
    message,
    meta,
  }) as LogRecord;

const settle = (ms = 30): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

describe.skipIf(!nativeAddon())("plugins on the Rust core", () => {
  it("adaptive sampling always keeps errors and drops the rest at a zero rate", () => {
    const plugin = new AdaptiveSamplingPlugin({ baseRate: 0, minRate: 0, rng: () => 0.99 });
    expect(plugin.beforeLog(record("INFO", "quiet"))).toBeNull();
    expect(plugin.beforeLog(record("ERROR", "boom"))?.message).toBe("boom");
    expect(plugin.rate).toBe(0);
  });

  it("the flight recorder ships a run's debug records only if the run errors", async () => {
    const sink = new CollectingTransport();
    const logger = new Logger("app", {
      level: Level.TRACE,
      transports: [sink],
      plugins: [new FlightRecorderPlugin({ transports: [sink] })],
    });
    logger.debug("quiet debug", { run_id: "passing" });
    logger.info("fine", { run_id: "passing" });
    logger.debug("debug context", { run_id: "failing" });
    logger.error("it broke", { run_id: "failing" });
    await logger.flush();
    const messages = sink.records.map((r) => r.message);
    expect(messages).toContain("debug context");
    expect(messages).toContain("it broke");
    expect(messages).toContain("fine");
    expect(messages).not.toContain("quiet debug");
  });

  it("the run summary totals a run and writes one record when the root span closes", () => {
    const sink = new CollectingTransport();
    const plugin = new RunSummaryPlugin({ transports: [sink] });
    plugin.afterLog({
      ...record("INFO", "call", { run_id: "r1", kind: "action", tool: "search", span_id: "x1" }),
      llm: { tokens_in: 10, tokens_out: 5, cost_usd: 0.5 },
    } as LogRecord);
    plugin.afterLog(record("ERROR", "oops", { run_id: "r1", retry_count: 2 }));
    expect(sink.records).toHaveLength(0);
    plugin.afterLog(record("INFO", "root", { run_id: "r1", kind: "span", span_id: "s0" }));
    expect(sink.records).toHaveLength(1);
    const summary = sink.records[0]!;
    expect(summary.logger).toBe("app.run_summary");
    expect(summary.meta).toMatchObject({
      run_id: "r1",
      tokens_in: 10,
      errors: 1,
      retries: 2,
      tool_calls: 1,
    });
  });
});

describe.skipIf(!nativeAddon())("metrics", () => {
  it("counts errors and feeds durations, tagged by logger", () => {
    const calls: unknown[] = [];
    const backend: MetricsBackend = {
      increment: (name, tags) => calls.push(["inc", name, tags]),
      observe: (name, value, tags) => calls.push(["obs", name, value, tags]),
    };
    const plugin = new MetricsPlugin(backend);
    plugin.afterLog(record("INFO", "fine"));
    plugin.afterLog(record("ERROR", "boom"));
    plugin.afterLog(record("INFO", "span closed", { duration_ms: 12.5 }));
    expect(calls).toEqual([
      ["inc", "logquill.errors", { logger: "app" }],
      ["obs", "logquill.duration_ms", 12.5, { logger: "app" }],
    ]);
  });

  it.skipIf(!nativeAddon()?.StatsdBackend)("StatsD sends counters and histograms over UDP", async () => {
    const server = createSocket("udp4");
    const received: string[] = [];
    server.on("message", (message) => received.push(message.toString()));
    await new Promise<void>((resolve) => server.bind(0, "127.0.0.1", resolve));
    const backend = new StatsDBackend({
      host: "127.0.0.1",
      port: (server.address() as AddressInfo).port,
      prefix: "app",
    });
    backend.increment("errors", { logger: "api" });
    backend.observe("latency", 12.5, { logger: "api" });
    await settle(100);
    server.close();
    expect(received.join("\n")).toMatch(/errors:1\|c\|#logger:api/);
    expect(received.join("\n")).toMatch(/latency:12\.5\|h\|#logger:api/);
  });

  it.skipIf(!nativeAddon()?.PushgatewayBackend)("the pushgateway backend accumulates series and PUTs them on flush", async () => {
    // The gateway runs in another process: flush blocks this one's thread while it waits for the reply.
    const gateway = spawn(
      process.execPath,
      [
        "-e",
        `const s = require("http").createServer((req, res) => {
          let body = ""; req.on("data", (c) => (body += c));
          req.on("end", () => { res.end(); console.log(JSON.stringify({ method: req.method, url: req.url, body })); process.exit(0); });
        });
        s.listen(0, "127.0.0.1", () => console.log(s.address().port));`,
      ],
      { stdio: ["ignore", "pipe", "inherit"] },
    );
    const output: string[] = [];
    let buffer = "";
    gateway.stdout.on("data", (chunk: Buffer) => {
      buffer += chunk.toString();
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      output.push(...lines);
    });
    for (let i = 0; i < 100 && output.length === 0; i++) await settle(20);
    const backend = new PrometheusPushgatewayBackend({
      gatewayUrl: `http://127.0.0.1:${output[0]!}`,
      jobName: "test",
    });
    backend.increment("logquill_errors", { logger: "api" });
    backend.observe("logquill_latency", 4, { logger: "api" });
    backend.flush();
    for (let i = 0; i < 100 && output.length < 2; i++) await settle(20);
    const request = JSON.parse(output[1]!) as { method: string; url: string; body: string };
    expect(request.method).toBe("PUT");
    expect(request.url).toBe("/metrics/job/test");
    expect(request.body).toContain('logquill_errors{logger="api"} 1');
    expect(request.body).toContain('logquill_latency_sum{logger="api"} 4');
    expect(backend.droppedSamples).toBe(0);
  });
});

describe.skipIf(!nativeAddon())("AuditLogger and signed chains", () => {
  const written = async () => {
    const sink = new CollectingTransport();
    const logger = new AuditLogger("audit", { transports: [sink] });
    logger.info("first", { password: "hunter2", note: "mail jo@example.com" });
    logger.info("second", { input_messages: [{ role: "user", content: "secret prompt" }] });
    logger.child("sub").info("third");
    await logger.flush();
    return { logger, sink, lines: sink.formatted };
  };

  it("redacts secrets and PII, captures no content, and chains every record", async () => {
    const { sink, lines, logger } = await written();
    const joined = lines.join("\n");
    expect(joined).not.toContain("hunter2");
    expect(joined).not.toContain("jo@example.com");
    expect(joined).not.toContain("secret prompt");
    expect(sink.records.every((r) => typeof r.meta.hash === "string")).toBe(true);
    expect(verifyChain(lines)).toMatchObject({
      ok: true,
      recordsChecked: 3,
      headHash: logger.headHash,
    });
  });

  it("a signed head catches a log with its end cut off, and an edit anywhere", async () => {
    const { logger, lines } = await written();
    const signature = logger.signHead("audit-key");
    expect(verifyHeadSignature(logger.headHash, "audit-key", signature)).toBe(true);
    expect(verifySignedChain(lines, "audit-key", signature).ok).toBe(true);
    expect(verifySignedChain(lines.slice(0, 2), "audit-key", signature).ok).toBe(false);
    expect(verifySignedChain(lines, "wrong-key", signature).ok).toBe(false);
    const edited = [...lines];
    edited[1] = edited[1]!.replace("second", "changed");
    expect(verifyChain(edited).ok).toBe(false);
  });
});

describe.skipIf(!nativeAddon())("graph transports", () => {
  const spans = (logger: Logger): void => {
    logger.info("child step", { run_id: "r1", span_id: "c1", parent_span_id: "root" });
    logger.info("root done", { run_id: "r1", span_id: "root" });
    logger.info("plain line", { run_id: "r1" });
  };

  it("Neo4j runs one UNWIND statement per batch with keyed rows and fresh keys for plain lines", async () => {
    const runs: {
      query: string;
      parameters: { rows: { key: string; parent_key: string | null }[] };
    }[] = [];
    const driver = {
      session: () => ({
        run: (query: string, parameters: never) => {
          runs.push({ query, parameters });
          return Promise.resolve();
        },
        close: () => Promise.resolve(),
      }),
      close: () => Promise.resolve(),
    };
    const transport = new Neo4jTransport({ driver, maxRecords: 3 });
    spans(new Logger("agent", { transports: [transport] }));
    await settle();
    expect(runs).toHaveLength(1);
    expect(runs[0]!.query).toBe(nativeAddon()!.CYPHER);
    const keys = runs[0]!.parameters.rows.map((row) => row.key);
    expect(keys.slice(0, 2)).toEqual(["c1", "root"]);
    expect(keys[2]).toMatch(/^leaf-[0-9a-f]{32}$/);
    expect(runs[0]!.parameters.rows[0]!.parent_key).toBe("root");
  });

  it("FalkorDB and Neptune receive the same rows through their own client shapes", async () => {
    const falkor: { name: string; query: string; params: unknown }[] = [];
    const client = {
      selectGraph: (name: string) => ({
        query: (query: string, options?: { params?: unknown }) => {
          falkor.push({ name, query, params: options?.params });
          return Promise.resolve();
        },
      }),
    };
    const neptune: { query: string; parameters: string }[] = [];
    spans(
      new Logger("a", { transports: [new FalkorDBTransport({ client, graph: "g", maxRecords: 3 })] }),
    );
    spans(
      new Logger("b", {
        transports: [
          new NeptuneTransport({
            maxRecords: 3,
            run: (query, parameters) => void neptune.push({ query, parameters }),
          }),
        ],
      }),
    );
    await settle();
    expect(falkor).toHaveLength(1);
    expect(falkor[0]!.name).toBe("g");
    expect(neptune).toHaveLength(1);
    expect((JSON.parse(neptune[0]!.parameters) as { rows: unknown[] }).rows).toHaveLength(3);
  });

  it("a transport that fails does not throw into the caller", async () => {
    const transport = new NeptuneTransport({
      maxRecords: 1,
      run: () => {
        throw new Error("neptune is down");
      },
    });
    const logger = new Logger("agent", { transports: [transport] });
    expect(() => logger.info("still fine", { run_id: "r1", span_id: "s" })).not.toThrow();
    await settle();
  });
});
