// The SDK's alert de-duplication against the shared corpus (`schema/corpus/alert-dedupe.json`). The JavaScript
// plugin is timer-driven, so each scenario runs under fake timers that stand in for the corpus's clock.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LogRecord } from "../../src/core/records.js";
import { AlertingPlugin } from "../../src/plugins/alerting-plugin.js";

interface Case {
  name: string;
  input: {
    options?: Record<string, any>;
    steps: { at: number; record?: LogRecord }[];
    end?: number;
    close?: boolean;
  };
  expected: {
    steps: { sent: { message: string; occurrences: number }[] }[];
    after: { message: string; occurrences: number }[];
    tracked: number;
  };
}

const file = join(
  dirname(fileURLToPath(import.meta.url)),
  "..", "..", "..", "..", "..",
  "schema", "corpus", "alert-dedupe.json",
);
const cases = (JSON.parse(readFileSync(file, "utf8")) as { cases: Case[] }).cases;

class Capture extends AlertingPlugin {
  sent: { message: string; occurrences: number }[] = [];
  protected sendAlert(record: LogRecord, occurrences: number): void {
    this.sent.push({ message: record.message, occurrences });
  }
}

describe("AlertingPlugin de-duplication", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  for (const { name, input, expected } of cases) {
    it(name, async () => {
      const o = input.options ?? {};
      const plugin = new Capture({
        ...(o.threshold ? { threshold: o.threshold } : {}),
        ...(o.dedupe_window_seconds === undefined ? {} : { dedupeWindowMs: o.dedupe_window_seconds * 1000 }),
        ...(o.max_tracked_keys === undefined ? {} : { maxTrackedKeys: o.max_tracked_keys }),
        ...(o.key === "message" ? { dedupeKey: (r: LogRecord) => r.message } : {}),
      });
      const steps: { sent: { message: string; occurrences: number }[] }[] = [];
      for (const step of input.steps) {
        // The core emits a window's summary when the next record arrives; this plugin's timer fires when the
        // window ends, which is earlier. Both are the same alert, so what is sent while the clock moves up to
        // this step counts as this step's.
        const before = plugin.sent.length;
        await vi.advanceTimersByTimeAsync(step.at * 1000 - Date.now());
        if (step.record) plugin.afterLog(structuredClone(step.record));
        await vi.advanceTimersByTimeAsync(0);
        steps.push({ sent: plugin.sent.slice(before) });
      }
      let after: { message: string; occurrences: number }[] = [];
      if (input.end !== undefined) {
        const before = plugin.sent.length;
        await vi.advanceTimersByTimeAsync(input.end * 1000 - Date.now());
        after = plugin.sent.slice(before);
      } else if (input.close) {
        plugin.close();
      }
      expect({ steps, after }).toEqual({ steps: expected.steps, after: expected.after });
    });
  }
});
