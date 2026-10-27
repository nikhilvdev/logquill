import { createSocket } from "node:dgram";
import { createServer, type IncomingMessage } from "node:http";
import type { AddressInfo } from "node:net";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { Logger } from "../../src/core/logger.js";
import { nativeAddon } from "../../src/core/native.js";
import {
  RustAppInsightsTransport,
  RustDatadogTransport,
  RustElasticsearchTransport,
  RustNewRelicTransport,
  RustOTLPLogsTransport,
  RustOTLPTraceTransport,
  RustSQLiteTransport,
  RustSyslogTransport,
} from "../../src/transports/native-sinks.js";

const runtime = Boolean(nativeAddon()?.syslog);

/** A local HTTP server in this process: the Rust worker posts from its own thread, so the event loop is free to answer. */
async function collector() {
  const requests: { method: string; url: string; body: string }[] = [];
  const server = createServer((request: IncomingMessage, response) => {
    let body = "";
    request.on("data", (chunk: Buffer) => (body += chunk.toString()));
    request.on("end", () => {
      requests.push({ method: request.method ?? "", url: request.url ?? "", body });
      response.end("{}");
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  return { requests, base, stop: () => server.close() };
}

const until = async (done: () => boolean): Promise<void> => {
  for (let i = 0; i < 100 && !done(); i++) await new Promise((resolve) => setTimeout(resolve, 20));
};

describe.skipIf(!runtime)("Rust-backed sinks", () => {
  it("SQLite: records are inserted as rows a query can read back", async () => {
    const { DatabaseSync } = await import("node:sqlite");
    const file = join(mkdtempSync(join(tmpdir(), "lq-sqlite-")), "logs.db");
    const sink = new RustSQLiteTransport({ filename: file, ensureSchema: true, maxRecords: 2 });
    const logger = new Logger("agent", { transports: [sink] });
    logger.info("first", { run_id: "r1", span_id: "s1" });
    logger.error("second", { run_id: "r1" });
    logger.info("third");
    await logger.close();
    const db = new DatabaseSync(file);
    const rows = db.prepare("SELECT level, message, run_id FROM logs ORDER BY rowid").all() as {
      level: string;
      message: string;
      run_id: string | null;
    }[];
    db.close();
    expect(rows.map((r) => [r.level, r.message])).toEqual([
      ["INFO", "first"],
      ["ERROR", "second"],
      ["INFO", "third"],
    ]);
    expect(rows[0]!.run_id).toBe("r1");
  });

  it("SQLite refuses a table name that is not an identifier", () => {
    expect(() => new RustSQLiteTransport({ tableName: "logs; DROP TABLE users" })).toThrow();
  });

  it("syslog: a UDP datagram carries the RFC 5424 priority and the record", async () => {
    const server = createSocket("udp4");
    const received: string[] = [];
    server.on("message", (message) => received.push(message.toString()));
    await new Promise<void>((resolve) => server.bind(0, "127.0.0.1", resolve));
    const sink = new RustSyslogTransport({
      host: "127.0.0.1",
      port: (server.address() as AddressInfo).port,
      appName: "api",
    });
    const logger = new Logger("agent", { transports: [sink] });
    logger.warn("disk almost full", { pct: 91 });
    await logger.flush();
    await until(() => received.length > 0);
    server.close();
    expect(received[0]).toMatch(/^<\d+>1 /);
    expect(received[0]).toContain("disk almost full");
  });

  it("Elasticsearch: a bulk request of ECS documents", async () => {
    const server = await collector();
    const sink = new RustElasticsearchTransport(server.base, { index: "app-logs", maxRecords: 1 });
    const logger = new Logger("agent", { transports: [sink] });
    logger.info("hello", { run_id: "r1" });
    await logger.flush();
    await until(() => server.requests.length > 0);
    server.stop();
    expect(server.requests[0]!.method).toBe("POST");
    expect(server.requests[0]!.url).toContain("_bulk");
    expect(server.requests[0]!.body).toContain("app-logs");
    expect(server.requests[0]!.body).toContain("@timestamp");
    expect(server.requests[0]!.body).toContain('"hello"');
  });

  it("OTLP: spans go to the traces endpoint and every record to the logs endpoint", async () => {
    const server = await collector();
    const traces = new RustOTLPTraceTransport({
      endpoint: `${server.base}/v1/traces`,
      serviceName: "svc",
      maxRecords: 1,
    });
    const logs = new RustOTLPLogsTransport({ endpoint: `${server.base}/v1/logs`, maxRecords: 1 });
    const logger = new Logger("agent", { transports: [traces, logs] });
    await logger.span("work", () => {
      logger.info("inside", { run_id: "r1" });
    });
    await logger.flush();
    await until(() => server.requests.length >= 2);
    server.stop();
    const byUrl = (path: string) => server.requests.filter((r) => r.url === path);
    expect(byUrl("/v1/traces").length).toBeGreaterThan(0);
    expect(byUrl("/v1/traces")[0]!.body).toContain("resourceSpans");
    expect(byUrl("/v1/traces")[0]!.body).toContain('"svc"');
    expect(byUrl("/v1/logs").length).toBeGreaterThan(0);
    expect(byUrl("/v1/logs")[0]!.body).toContain("resourceLogs");
  });

  it("the fixed-endpoint cloud sinks construct, and validate what they can offline", () => {
    expect(() => new RustDatadogTransport("key", { site: "datadoghq.eu" })).not.toThrow();
    expect(() => new RustAppInsightsTransport("00000000-0000-0000-0000-000000000000")).not.toThrow();
    expect(() => new RustNewRelicTransport("key", { region: "EU" })).not.toThrow();
    // @ts-expect-error a region that does not exist
    expect(() => new RustNewRelicTransport("key", { region: "MARS" })).toThrow();
  });
});
