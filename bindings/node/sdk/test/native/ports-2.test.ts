// Python tests for config, syslog and the trace tree that the Node suite did not yet pin (test_config.py,
// test_syslog_transport.py, test_trace_tree.py). The tree itself is built in Rust; its 34 shared corpus cases
// already run, so these check the pieces a user touches: nesting, rollup, and bad input.
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { loadConfig, loggerFromEnv, Logger, SyslogTransport, TextFormatter } from "../../src/index.js";
import { nativeAddon } from "../../src/core/native.js";

const dir = mkdtempSync(join(tmpdir(), "lq-ports2-"));

describe("config (test_config.py)", () => {
  const saved = { ...process.env };
  afterEach(() => {
    for (const key of Object.keys(process.env)) if (!(key in saved)) delete process.env[key];
    Object.assign(process.env, saved);
  });

  it("an unknown formatter name lists the valid ones", async () => {
    await expect(loadConfig({ transports: [{ type: "console", options: { formatter: "xml" } }] })).rejects.toThrow(/built-in formatters are/);
  });

  it("a transport can name a built-in formatter", async () => {
    const logger = await loadConfig({ transports: [{ type: "console", options: { formatter: "text" } }] });
    const transport = (logger as unknown as { transports: { formatter: unknown }[] }).transports[0]!;
    expect(transport.formatter).toBeInstanceOf(TextFormatter);
  });

  it("an unknown transport type raises a helpful error", async () => {
    await expect(loadConfig({ transports: [{ type: "carrier-pigeon" }] })).rejects.toThrow(/carrier-pigeon/);
  });

  it("a class entry without a '#' or with a missing export raises", async () => {
    await expect(loadConfig({ transports: [{ class: "no-hash-here" }] })).rejects.toThrow();
    await expect(loadConfig({ transports: [{ class: "node:path#NotThere" }] })).rejects.toThrow(/NotThere/);
  });

  it("loggerFromEnv with nothing set returns a default logger", async () => {
    delete process.env.LOGQUILL_LEVEL;
    delete process.env.LOGQUILL_CONFIG_FILE;
    const logger = await loggerFromEnv();
    expect(logger.name).toBe("app");
  });

  it("loggerFromEnv honours a custom prefix", async () => {
    process.env.MYAPP_LEVEL = "ERROR";
    const logger = await loggerFromEnv("MYAPP_", "svc");
    expect(logger.name).toBe("svc");
    expect(logger.error("kept")).toBeDefined();
    expect(logger.info("filtered")).toBeNull();
  });

  it("the file's name key overrides the name argument", async () => {
    const path = join(dir, "named.json");
    writeFileSync(path, JSON.stringify({ name: "from-file" }));
    process.env.LOGQUILL_CONFIG_FILE = path;
    expect((await loggerFromEnv("LOGQUILL_", "arg")).name).toBe("from-file");
  });
});

describe("syslog (test_syslog_transport.py)", () => {
  const send = async (options: ConstructorParameters<typeof SyslogTransport>[0], loggerName = "app") => {
    const sent: string[] = [];
    const logger = new Logger(loggerName, { transports: [new SyslogTransport({ ...options, sender: (d) => sent.push(d.toString()) })] });
    logger.info("hello");
    await logger.flush();
    return sent[0] ?? "";
  };

  it("the app name defaults to the logger name, and an explicit one overrides it", async () => {
    expect(await send({}, "billing")).toMatch(/ billing \d+ - - /);
    expect(await send({ appName: "custom" }, "billing")).toMatch(/ custom \d+ - - /);
  });

  it("the default facility is user (1) and PRI follows facility and severity", async () => {
    expect(await send({})).toMatch(/^<14>1 /); // 1 * 8 + INFO severity 6
    expect(await send({ facility: 3 })).toMatch(/^<30>1 /);
  });

  it("the message body carries the formatted record", async () => {
    expect(await send({})).toContain('"message":"hello"');
  });

  it("UDP messages have no trailing newline", async () => {
    expect((await send({ protocol: "udp" })).endsWith("\n")).toBe(false);
  });
});

describe.skipIf(!nativeAddon())("trace tree (test_trace_tree.py)", () => {
  const lines = (records: object[]) => JSON.stringify(records);
  const rec = (meta: Record<string, unknown>, extra: Record<string, unknown> = {}) => ({
    schema_version: "2.0", timestamp: "2026-01-01T00:00:00.000Z", level: "INFO", logger: "a", message: "m", meta: { run_id: "r1", ...meta }, ...extra,
  });
  type Node = { children: Node[] };
  const build = (records: object[], runId = "r1") =>
    JSON.parse(nativeAddon()!.buildTrace(lines(records), runId)) as { record_count: number; roots: Node[]; text: string };

  it("a full run reconstructs into one root matching the span nesting", () => {
    const tree = build([
      rec({ span_id: "c", parent_span_id: "b", duration_ms: 1 }),
      rec({ span_id: "b", parent_span_id: "a", duration_ms: 2 }),
      rec({ span_id: "a", duration_ms: 3 }),
    ]);
    expect(tree.roots).toHaveLength(1);
    expect(tree.roots[0]!.children).toHaveLength(1);
    expect(tree.roots[0]!.children[0]!.children).toHaveLength(1);
  });

  it("tokens and cost roll up from every descendant; a span with no LLM descendants shows none", () => {
    const tree = build([
      rec({ span_id: "leaf1", parent_span_id: "root", duration_ms: 1 }, { llm: { tokens_in: 10, tokens_out: 5, cost_usd: 0.25 } }),
      rec({ span_id: "leaf2", parent_span_id: "root", duration_ms: 1 }, { llm: { tokens_in: 1, tokens_out: 2, cost_usd: 0.5 } }),
      rec({ span_id: "root", duration_ms: 9 }),
    ]);
    expect(tree.text).toContain("0.75");
    expect(build([rec({ span_id: "solo", duration_ms: 1 })]).text).not.toContain("0.75");
  });

  it("records of other runs are skipped, and a record with no meta does not crash", () => {
    const tree = build([rec({ span_id: "a", duration_ms: 1 }), rec({ span_id: "z", run_id: "other" }), { level: "INFO", message: "bare" }]);
    expect(tree.record_count).toBe(1);
  });

  it("two records sharing a span id do not crash the builder", () => {
    expect(() => build([rec({ span_id: "dup", duration_ms: 1 }), rec({ span_id: "dup", duration_ms: 2 })])).not.toThrow();
  });

  it("records with no span at all are still roots, in order", () => {
    const tree = build([rec({}, { message: "first" }), rec({}, { message: "second" })]);
    expect(tree.roots).toHaveLength(2);
    expect(tree.text.indexOf("first")).toBeLessThan(tree.text.indexOf("second"));
  });
});
