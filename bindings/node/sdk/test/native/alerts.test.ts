import { createServer as createHttp, type IncomingMessage } from "node:http";
import { createServer as createTcp, type Socket } from "node:net";
import type { AddressInfo } from "node:net";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { Level } from "../../src/core/levels.js";
import { Logger } from "../../src/core/logger.js";
import { nativeAddon } from "../../src/core/native.js";
import {
  RustEmailAlertPlugin,
  RustLevelWatcher,
  RustPagerDutyAlertPlugin,
  RustSlackAlertPlugin,
} from "../../src/plugins/native-alerts.js";

const runtime = Boolean(nativeAddon()?.Alerting);
const until = async (done: () => boolean, tries = 150): Promise<void> => {
  for (let i = 0; i < tries && !done(); i++) await new Promise((resolve) => setTimeout(resolve, 20));
};

/** A local HTTP server in this process; the Rust worker posts from its own thread, so the event loop can answer. */
async function collector() {
  const requests: { method: string; url: string; body: string }[] = [];
  const server = createHttp((request: IncomingMessage, response) => {
    let body = "";
    request.on("data", (chunk: Buffer) => (body += chunk.toString()));
    request.on("end", () => {
      requests.push({ method: request.method ?? "", url: request.url ?? "", body });
      response.statusCode = 202;
      response.end("{}");
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { requests, url: `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`, stop: () => server.close() };
}

/** A minimal SMTP server: enough of the protocol for one message, recording what it was sent. */
async function smtp() {
  const received: string[] = [];
  const server = createTcp((socket: Socket) => {
    let data = false;
    let buffer = "";
    socket.write("220 test ESMTP\r\n");
    socket.on("data", (chunk: Buffer) => {
      buffer += chunk.toString();
      for (;;) {
        if (data) {
          const end = buffer.indexOf("\r\n.\r\n");
          if (end < 0) return;
          received.push(buffer.slice(0, end));
          buffer = buffer.slice(end + 5);
          data = false;
          socket.write("250 queued\r\n");
          continue;
        }
        const eol = buffer.indexOf("\r\n");
        if (eol < 0) return;
        const line = buffer.slice(0, eol);
        buffer = buffer.slice(eol + 2);
        received.push(line);
        if (/^(EHLO|HELO)/i.test(line)) socket.write("250 test\r\n");
        else if (/^DATA/i.test(line)) (data = true), socket.write("354 go\r\n");
        else if (/^QUIT/i.test(line)) socket.end("221 bye\r\n");
        else socket.write("250 ok\r\n");
      }
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { received, port: (server.address() as AddressInfo).port, stop: () => server.close() };
}

describe.skipIf(!runtime)("Rust alert plugins", () => {
  it("Slack: the first error posts to the webhook; repeats inside the window are collapsed", async () => {
    const server = await collector();
    const plugin = new RustSlackAlertPlugin({ webhookUrl: `${server.url}/hook` });
    const logger = new Logger("api", { plugins: [plugin] });
    for (let i = 0; i < 3; i++) logger.error("database is down");
    await logger.flush();
    await plugin.flush();
    await until(() => server.requests.length > 0);
    await new Promise((resolve) => setTimeout(resolve, 100)); // a (wrong) second request would have arrived by now
    server.stop();
    plugin.close();
    expect(server.requests).toHaveLength(1);
    expect(server.requests[0]).toMatchObject({ method: "POST", url: "/hook" });
    expect(server.requests[0]!.body).toContain("database is down");
  });

  it("only records at or above the threshold alert, and byMessage collapses across loggers", async () => {
    const server = await collector();
    const plugin = new RustSlackAlertPlugin({ webhookUrl: `${server.url}/hook`, threshold: "WARN", byMessage: true });
    const a = new Logger("a", { plugins: [plugin] });
    const b = new Logger("b", { plugins: [plugin] });
    a.info("ignored");
    a.warn("same message");
    b.warn("same message");
    await plugin.flush();
    await until(() => server.requests.length > 0);
    await new Promise((resolve) => setTimeout(resolve, 100));
    server.stop();
    plugin.close();
    expect(server.requests).toHaveLength(1);
    expect(server.requests[0]!.body).toContain("same message");
  });

  it("PagerDuty: a trigger event to the configured endpoint with the routing key", async () => {
    const server = await collector();
    const plugin = new RustPagerDutyAlertPlugin({ routingKey: "rk-123", url: `${server.url}/v2/enqueue` });
    new Logger("billing", { plugins: [plugin] }).fatal("ledger out of balance");
    await plugin.flush();
    await until(() => server.requests.length > 0);
    server.stop();
    plugin.close();
    const request = server.requests[0]!;
    expect(request.url).toBe("/v2/enqueue");
    expect(request.body).toContain("rk-123");
    expect(request.body).toContain("trigger");
    expect(request.body).toContain("ledger out of balance");
  });

  it("email: a message to each recipient through an SMTP server", async () => {
    const server = await smtp();
    const plugin = new RustEmailAlertPlugin({
      smtpHost: "127.0.0.1",
      smtpPort: server.port,
      fromAddr: "alerts@example.com",
      toAddrs: ["oncall@example.com", "lead@example.com"],
      useTls: false,
    });
    new Logger("api", { plugins: [plugin] }).error("queue is stuck");
    await plugin.flush();
    await until(() => server.received.some((line) => line.includes("queue is stuck")));
    server.stop();
    plugin.close();
    const all = server.received.join("\n");
    expect(all).toMatch(/MAIL FROM:<alerts@example\.com>/i);
    expect(all).toMatch(/RCPT TO:<oncall@example\.com>/i);
    expect(all).toMatch(/RCPT TO:<lead@example\.com>/i);
    expect(all).toContain("queue is stuck");
  });

  it("an unreachable destination never throws into the caller", async () => {
    const plugin = new RustSlackAlertPlugin({ webhookUrl: "http://127.0.0.1:1/hook", timeout: 1 });
    const logger = new Logger("api", { plugins: [plugin] });
    expect(() => logger.error("nobody home")).not.toThrow();
    await plugin.flush(3000);
    plugin.close();
  });

  it("an unknown threshold is refused with the level named", () => {
    expect(() => new RustSlackAlertPlugin({ webhookUrl: "http://x", threshold: "LOUD" })).toThrow(/LOUD/);
  });
});

describe.skipIf(!Boolean(nativeAddon()?.LevelWatcher))("Rust level watcher", () => {
  const file = (text: string) => {
    const path = join(mkdtempSync(join(tmpdir(), "lq-level-")), "level");
    writeFileSync(path, text);
    return path;
  };

  it("applies a level from a file, and again when the file changes, without a restart", async () => {
    const path = file("DEBUG\n");
    const logger = new Logger("svc", { level: Level.INFO });
    const watcher = new RustLevelWatcher(logger, { file: path, intervalSeconds: 0.05 }).start();
    await until(() => logger.level === Level.DEBUG);
    expect(logger.level).toBe(Level.DEBUG);
    writeFileSync(path, "30"); // a weight works too
    await until(() => logger.level === Level.WARN);
    expect(logger.level).toBe(Level.WARN);
    watcher.stop();
  });

  it("an invalid value is reported and the level is left alone; a missing file means no change", async () => {
    const complaints: string[] = [];
    const logger = new Logger("svc", { level: Level.ERROR });
    const path = file("loud");
    const watcher = new RustLevelWatcher(logger, { file: path, onInvalid: (m) => void complaints.push(m) });
    watcher.pollOnce();
    await until(() => complaints.length > 0);
    expect(complaints[0]).toMatch(/not a valid level/);
    expect(logger.level).toBe(Level.ERROR);
    const missing = new RustLevelWatcher(logger, { file: join(tmpdir(), "lq-no-such-level-file") });
    missing.pollOnce();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(logger.level).toBe(Level.ERROR);
    watcher.stop();
  });

  it("reads an environment variable", async () => {
    process.env.LQ_TEST_LEVEL = "trace";
    const logger = new Logger("svc", { level: Level.INFO });
    const watcher = new RustLevelWatcher(logger, { env: "LQ_TEST_LEVEL" });
    watcher.pollOnce();
    await until(() => logger.level === Level.TRACE);
    delete process.env.LQ_TEST_LEVEL;
    expect(logger.level).toBe(Level.TRACE);
  });

  it("needs exactly one of file and env", () => {
    const logger = new Logger("svc");
    expect(() => new RustLevelWatcher(logger, {})).toThrow(/exactly one/);
    expect(() => new RustLevelWatcher(logger, { file: "a", env: "B" })).toThrow(/exactly one/);
  });
});
