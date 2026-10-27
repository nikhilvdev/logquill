// The SDK's stateful plugins against the shared corpus: sampling with tail elevation, the rate limiter, the
// run stamp and the tamper chain. Each scenario is stepped through the same injected clock and random draw
// as the Rust reference (`crates/logquill-core/tests/processing.rs`). Runs with and without the addon.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { LogRecord } from "../../src/core/records.js";
import { RateLimitPlugin } from "../../src/plugins/rate-limit-plugin.js";
import { RunPlugin } from "../../src/plugins/run-plugin.js";
import { SamplingPlugin } from "../../src/plugins/sampling-plugin.js";
import { TamperEvidentPlugin } from "../../src/plugins/tamper-evident-plugin.js";
import { CollectingTransport } from "../../src/transports/transport.js";

interface Step {
  record: LogRecord;
  clock?: number;
  rng?: number;
}
interface Case {
  name: string;
  input: Record<string, any> & { steps: Step[] };
  expected: Record<string, any>;
}

const dir = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..", "..", "schema", "corpus");
const cases = (group: string): Case[] =>
  (JSON.parse(readFileSync(join(dir, `${group}.json`), "utf8")) as { cases: Case[] }).cases;
const copy = (record: LogRecord): LogRecord => structuredClone(record);

describe("SamplingPlugin", () => {
  for (const { name, input, expected } of cases("sampling")) {
    it(name, () => {
      const o = input.options ?? {};
      let draw = 0;
      const sink = new CollectingTransport();
      const make = () =>
        new SamplingPlugin(o.rate ?? 0, {
          rng: () => draw,
          ...(o.trace_key ? { traceKey: o.trace_key } : {}),
          ...(o.elevate_at ? { elevateAt: o.elevate_at } : {}),
          ...(o.tail ? { transports: [sink] } : {}),
          ...(o.max_buffered_records ? { maxBufferedRecords: o.max_buffered_records } : {}),
          ...(o.max_traces ? { maxTraces: o.max_traces } : {}),
          ...(o.max_elevated ? { maxElevated: o.max_elevated } : {}),
        });
      if (expected.error) {
        expect(make).toThrow(expected.error);
        return;
      }
      const plugin = make();
      const steps = input.steps.map((step) => {
        if (step.rng !== undefined) draw = step.rng;
        const before = sink.records.length;
        const kept = plugin.beforeLog(copy(step.record)) !== null;
        return { kept, written: sink.records.slice(before).map((r) => r.message) };
      });
      expect(steps).toEqual(expected.steps);
    });
  }
});

describe("RateLimitPlugin", () => {
  for (const { name, input, expected } of cases("rate-limit")) {
    it(name, () => {
      const o = input.options;
      let clock = input.start ?? 0;
      const keyFunc =
        o.key === "logger" ? (r: LogRecord) => r.logger : o.key === "message" ? (r: LogRecord) => r.message : undefined;
      const make = () =>
        new RateLimitPlugin(o.max_records, o.per_seconds ?? 1, {
          clock: () => clock,
          ...(o.max_keys ? { maxKeys: o.max_keys } : {}),
          ...(keyFunc ? { keyFunc } : {}),
        });
      if (expected.error) {
        // the core names its parameters in snake_case, this package in camelCase: only the refusal is compared
        expect(make).toThrow();
        return;
      }
      const plugin = make();
      const steps = input.steps.map((step) => {
        if (step.clock !== undefined) clock = step.clock;
        return { kept: plugin.beforeLog(copy(step.record)) !== null, written: [] };
      });
      expect(steps).toEqual(expected.steps);
    });
  }
});

describe("RunPlugin", () => {
  for (const { name, input, expected } of cases("run-plugin")) {
    it(name, () => {
      const given: string | undefined = input.options?.run_id;
      const plugin = new RunPlugin(given ? { runId: given } : {});
      const steps = input.steps.map((step) => {
        const meta = { ...plugin.beforeLog(copy(step.record)).meta };
        if (!given && meta.run_id === plugin.runId) meta.run_id = "<generated>";
        return { kept: true, meta, written: [] };
      });
      expect(steps).toEqual(expected.steps);
    });
  }
});

// A JavaScript number cannot tell `1.0` from `1`, so a record whose hash covers a float with no fractional
// part cannot be reproduced from parsed JSON (the corpus records this under `differs.js`).
const FLOAT_SPELLING = new Set([
  "non-ASCII, floats, big integers and an llm block",
  "an edited llm block",
]);

describe("TamperEvidentPlugin", () => {
  for (const { name, input, expected } of cases("hash-chain")) {
    const run = FLOAT_SPELLING.has(name) ? it.skip : it;
    run(name, () => {
      const plugin = new TamperEvidentPlugin(input.genesis ? { genesisHash: input.genesis } : {});
      const chain = (input.records as LogRecord[]).map((record) => plugin.beforeLog(copy(record)));
      expect(chain).toEqual(expected.chain);
      expect(plugin.headHash).toEqual(expected.head_hash);
    });
  }
});
