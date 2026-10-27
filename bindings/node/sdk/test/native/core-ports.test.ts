// Python tests (`docs/spec/legacy-tests/python-tests.txt`) for behaviour the Rust core provides, ported to Node
// where the shared corpus does not already pin it: adaptive sampling (test_adaptive_sampling_plugin), the tamper
// chain and signed heads (test_tamper_evident_plugin), and content policy (test_privacy).
import { describe, expect, it } from "vitest";
import { verifyChain, verifyHeadSignature, verifySignedChain } from "../../src/core/integrity.js";
import { Logger } from "../../src/core/logger.js";
import { nativeAddon } from "../../src/core/native.js";
import type { LogRecord } from "../../src/core/records.js";
import { AdaptiveSamplingPlugin } from "../../src/plugins/native-plugins.js";
import { GENESIS_HASH, TamperEvidentPlugin } from "../../src/plugins/tamper-evident-plugin.js";
import { CollectingTransport } from "../../src/transports/transport.js";

const core = Boolean(nativeAddon());
const rec = (level: string, message: string, meta: Record<string, unknown> = {}): LogRecord =>
  ({ schema_version: "2.0", timestamp: "2026-01-01T00:00:00.000Z", level, logger: "app", message, meta }) as LogRecord;

describe.skipIf(!core)("AdaptiveSamplingPlugin (test_adaptive_sampling_plugin.py)", () => {
  /** A plugin on a clock the test moves and a random draw it chooses. */
  function make(options: ConstructorParameters<typeof AdaptiveSamplingPlugin>[0] = {}) {
    const state = { now: 0, draw: 0.99 };
    const plugin = new AdaptiveSamplingPlugin({ ...options, now: () => state.now, rng: () => state.draw });
    return { plugin, state };
  }
  const kept = (plugin: AdaptiveSamplingPlugin, record: LogRecord) => plugin.beforeLog(record) !== null;

  it("a rate outside 0 to 1 raises, and so does a minimum above the maximum", () => {
    expect(() => new AdaptiveSamplingPlugin({ baseRate: 1.5 })).toThrow();
    expect(() => new AdaptiveSamplingPlugin({ baseRate: -0.1 })).toThrow();
    expect(() => new AdaptiveSamplingPlugin({ minRate: 0.9, maxRate: 0.1 })).toThrow();
  });

  it("ordinary INFO is dropped when the draw misses the rate, and kept when it hits", () => {
    const { plugin, state } = make({ baseRate: 0.5, minRate: 0.5, maxRate: 0.5 });
    state.draw = 0.9;
    expect(kept(plugin, rec("INFO", "a"))).toBe(false);
    state.draw = 0.1;
    expect(kept(plugin, rec("INFO", "b"))).toBe(true);
  });

  it("keepAt is configurable: records at or above it are always kept", () => {
    const { plugin } = make({ baseRate: 0, minRate: 0, keepAt: "WARN" });
    expect(kept(plugin, rec("WARN", "w"))).toBe(true);
    expect(kept(plugin, rec("INFO", "i"))).toBe(false);
    expect(kept(make({ baseRate: 0, minRate: 0 }).plugin, rec("WARN", "w"))).toBe(false); // ERROR by default
  });

  it("a slow span is kept; a non-span record is never treated as slow, even with a duration_ms", () => {
    const { plugin } = make({ baseRate: 0, minRate: 0, slowMs: 100 });
    expect(kept(plugin, rec("INFO", "slow", { kind: "span", duration_ms: 5000 }))).toBe(true);
    expect(kept(plugin, rec("INFO", "fast", { kind: "span", duration_ms: 5 }))).toBe(false);
    expect(kept(plugin, rec("INFO", "not a span", { duration_ms: 5000 }))).toBe(false);
  });

  const size = (record: LogRecord) => JSON.stringify(record).length;

  it("the byte budget caps what is kept within one window, and resets on a new one", () => {
    const { plugin, state } = make({ baseRate: 1, minRate: 1, maxRate: 1, maxBytesPerSecond: 1500, windowSeconds: 1 });
    const record = rec("INFO", "x".repeat(250));
    let keptNow = 0;
    for (let i = 0; i < 20; i++) if (kept(plugin, record)) keptNow++;
    expect(keptNow).toBeGreaterThan(0);
    expect(keptNow * size(record)).toBeLessThanOrEqual(1500 + size(record));
    expect(keptNow).toBeLessThan(20);
    state.now = 1.5; // a new window
    expect(kept(plugin, record)).toBe(true);
  });

  it("a record too big for an empty budget is dropped", () => {
    const { plugin } = make({ baseRate: 1, minRate: 1, maxRate: 1, maxBytesPerSecond: 10 });
    expect(kept(plugin, rec("INFO", "this record is far larger than ten bytes"))).toBe(false);
  });

  it("the rate falls after a window of high demand and rises after one of low demand", () => {
    const { plugin, state } = make({ baseRate: 0.5, minRate: 0.01, maxRate: 1, maxBytesPerSecond: 1000, windowSeconds: 1 });
    const start = plugin.rate;
    state.draw = 0; // every draw hits, so demand is actually emitted; the rate follows emitted bytes
    for (let i = 0; i < 60; i++) kept(plugin, rec("INFO", "y".repeat(200)));
    state.now = 1.5;
    kept(plugin, rec("INFO", "roll the window"));
    const afterHigh = plugin.rate;
    expect(afterHigh).toBeLessThan(start);
    state.now = 3; // one quiet window
    kept(plugin, rec("INFO", "tiny"));
    state.now = 4.5;
    kept(plugin, rec("INFO", "tiny"));
    expect(plugin.rate).toBeGreaterThan(afterHigh);
  });

  it("a long idle gap does not retroactively apply several windows' worth of adjustment", () => {
    const run = (gap: number) => {
      const { plugin, state } = make({ baseRate: 0.5, minRate: 0.01, maxRate: 1, maxBytesPerSecond: 1000, windowSeconds: 1 });
      for (let i = 0; i < 60; i++) kept(plugin, rec("INFO", "y".repeat(200)));
      state.now = gap;
      kept(plugin, rec("INFO", "after the gap"));
      return plugin.rate;
    };
    expect(run(100)).toBeCloseTo(run(1.5), 5);
  });
});

describe.skipIf(!core)("tamper chain and signed heads (test_tamper_evident_plugin.py)", () => {
  const chain = (count: number) => {
    const plugin = new TamperEvidentPlugin();
    const records = Array.from({ length: count }, (_, i) => plugin.beforeLog(rec("INFO", `m${String(i)}`, { i })));
    return { plugin, records, lines: records.map((r) => JSON.stringify(r)) };
  };

  it("the head hash starts at the genesis hash and tracks the last record", () => {
    const plugin = new TamperEvidentPlugin();
    expect(plugin.headHash).toBe(GENESIS_HASH);
    const record = plugin.beforeLog(rec("INFO", "a"));
    expect(plugin.headHash).toBe(record.meta.hash);
  });

  it("signHead is deterministic for the same key and state, and differs by key and by state", () => {
    const { plugin } = chain(2);
    expect(plugin.signHead("k")).toBe(plugin.signHead("k"));
    expect(plugin.signHead("k")).not.toBe(plugin.signHead("other"));
    const before = plugin.signHead("k");
    plugin.beforeLog(rec("INFO", "more"));
    expect(plugin.signHead("k")).not.toBe(before);
  });

  it("the detailed verification reports ok with the head hash, and where an edit broke the chain", () => {
    const { plugin, lines } = chain(4);
    expect(verifyChain(lines)).toMatchObject({ ok: true, recordsChecked: 4, headHash: plugin.headHash });
    const edited = [...lines];
    edited[2] = edited[2]!.replace("m2", "changed");
    const result = verifyChain(edited);
    expect(result.ok).toBe(false);
    expect(result.brokenAt).toBe(3);
    expect(result.reason).toBeTruthy();
  });

  it("the static verifier still returns a bare boolean", () => {
    const { records } = chain(3);
    expect(TamperEvidentPlugin.verifyChain(records)).toBe(true);
  });

  it("a signed chain passes with the right key and the full chain, fails on the wrong key, and catches a deleted trailing line", () => {
    const { plugin, lines } = chain(5);
    const signature = plugin.signHead("secret-key");
    expect(verifySignedChain(lines, "secret-key", signature).ok).toBe(true);
    expect(verifySignedChain(lines, "wrong-key", signature).ok).toBe(false);
    expect(verifySignedChain(lines.slice(0, -1), "secret-key", signature).ok).toBe(false);
  });

  it("a head signature is rejected when it is wrong, the wrong length, or empty", () => {
    const { plugin } = chain(1);
    const good = plugin.signHead("k");
    expect(verifyHeadSignature(plugin.headHash, "k", good)).toBe(true);
    expect(verifyHeadSignature(plugin.headHash, "k", good.replace(/.$/, good.endsWith("0") ? "1" : "0"))).toBe(false);
    expect(verifyHeadSignature(plugin.headHash, "k", good.slice(0, -2))).toBe(false);
    expect(verifyHeadSignature(plugin.headHash, "k", "")).toBe(false);
  });
});

describe.skipIf(!core)("content policy (test_privacy.py)", () => {
  const addon = () => nativeAddon()!;
  const logged = async (contentPolicy: "off" | "hash" | "truncate" | "full", meta: Record<string, unknown>) => {
    const sink = new CollectingTransport();
    const logger = new Logger("p", { contentPolicy, transports: [sink] });
    logger.info("m", meta);
    await logger.flush();
    return sink.records[0]!.meta;
  };

  it("the four policies are accepted and anything else is refused", () => {
    for (const policy of ["off", "hash", "truncate", "full"]) expect(() => new Logger("x", { contentPolicy: policy as never })).not.toThrow();
    expect(() => new Logger("x", { contentPolicy: "loud" as never })).toThrow();
  });

  it("hash is stable and never shows the raw value", async () => {
    const a = await logged("hash", { input_messages: "my secret prompt" });
    const b = await logged("hash", { input_messages: "my secret prompt" });
    const c = await logged("hash", { input_messages: "a different prompt" });
    expect(String(a.input_messages)).toMatch(/^sha256:/);
    expect(a.input_messages).toBe(b.input_messages);
    expect(a.input_messages).not.toBe(c.input_messages);
    expect(JSON.stringify(a)).not.toContain("my secret prompt");
  });

  it("truncate cuts long values with a marker and keeps short ones whole", () => {
    const long = JSON.parse(addon().applyPolicyValue(JSON.stringify("x".repeat(5000)), "truncate")) as string;
    expect(long.length).toBeLessThan(5000);
    expect(long.startsWith('"xxxx')).toBe(true); // truncation cuts the serialized text, so the opening quote stays
    expect(long).not.toBe("x".repeat(long.length)); // the marker
    expect(JSON.parse(addon().applyPolicyValue('"hello"', "truncate"))).toBe('"hello"'); // a short value comes back unchanged
  });

  it("full is a true no-op", async () => {
    const meta = await logged("full", { input_messages: [{ role: "user", content: "kept" }] });
    expect(meta.input_messages).toEqual([{ role: "user", content: "kept" }]);
  });

  it("off replaces the value, and the policy covers a span's captured state too", async () => {
    expect(String((await logged("off", { input_messages: "raw" })).input_messages)).not.toContain("raw");
    const sink = new CollectingTransport();
    const logger = new Logger("p", { contentPolicy: "off", transports: [sink] });
    let state = { secret: "before" };
    await logger.span("work", () => void (state = { secret: "after" }), { captureState: () => state });
    await logger.flush();
    expect(JSON.stringify(sink.records[0]!.meta.state_diff ?? "")).not.toMatch(/before|after/);
  });

  it("keysInClass returns every key tagged with that class", () => {
    for (const name of ["secret", "pii", "content"]) {
      const expected = addon().fieldClasses().filter((e) => e.class === name).map((e) => e.key);
      expect(addon().keysInClass(name)).toEqual([...expected].sort()); // keysInClass sorts; fieldClasses keeps declaration order
    }
  });

  it("a circular reference under a policy falls back instead of raising", async () => {
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    const sink = new CollectingTransport();
    const logger = new Logger("p", { contentPolicy: "hash", transports: [sink] });
    expect(() => logger.info("m", { input_messages: cyclic })).not.toThrow();
  });
});
