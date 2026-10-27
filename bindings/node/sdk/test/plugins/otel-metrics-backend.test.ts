import { describe, expect, it } from "vitest";
import { Logger } from "../../src/core/logger.js";
import type { LogRecord } from "../../src/core/records.js";
import { MetricsPlugin } from "../../src/plugins/metrics-plugin.js";
import { OTelMetricsBackend, type MeterLike } from "../../src/plugins/otel-metrics-backend.js";

function fakeMeter() {
  const calls: unknown[] = [];
  const created: string[] = [];
  const meter: MeterLike = {
    createCounter: (name) => {
      created.push(`counter:${name}`);
      return { add: (value, attributes) => void calls.push(["add", name, value, attributes]) };
    },
    createHistogram: (name) => {
      created.push(`histogram:${name}`);
      return { record: (value, attributes) => void calls.push(["record", name, value, attributes]) };
    },
  };
  return { meter, calls, created };
}

describe("OTelMetricsBackend", () => {
  it("increments a counter and records a histogram value, with the tags as attributes", () => {
    const { meter, calls } = fakeMeter();
    const backend = new OTelMetricsBackend({ meter });
    backend.increment("logquill.errors", { logger: "api" });
    backend.observe("logquill.duration_ms", 12.5, { logger: "api" });
    expect(calls).toEqual([
      ["add", "logquill.errors", 1, { logger: "api" }],
      ["record", "logquill.duration_ms", 12.5, { logger: "api" }],
    ]);
  });

  it("creates each instrument once", () => {
    const { meter, created } = fakeMeter();
    const backend = new OTelMetricsBackend({ meter });
    for (let i = 0; i < 5; i++) backend.increment("c", {});
    for (let i = 0; i < 5; i++) backend.observe("h", i, {});
    expect(created).toEqual(["counter:c", "histogram:h"]);
  });

  it("bounds the number of distinct instruments", () => {
    const { meter, created } = fakeMeter();
    const backend = new OTelMetricsBackend({ meter });
    for (let i = 0; i < 1500; i++) backend.increment(`metric.${String(i)}`, {});
    expect(created).toHaveLength(1000);
  });

  it("falls back to the global OpenTelemetry meter (a no-op unless a provider is registered)", () => {
    const backend = new OTelMetricsBackend();
    expect(() => {
      backend.increment("logquill.errors", { logger: "api" });
      backend.observe("logquill.duration_ms", 1, {});
    }).not.toThrow();
  });

  it("works as the backend of a MetricsPlugin", async () => {
    const { meter, calls } = fakeMeter();
    const logger = new Logger("api", { plugins: [new MetricsPlugin(new OTelMetricsBackend({ meter }))] });
    logger.error("boom");
    logger.info("span closed", { duration_ms: 3 } as LogRecord["meta"]);
    await logger.flush();
    expect(calls).toEqual([
      ["add", "logquill.errors", 1, { logger: "api" }],
      ["record", "logquill.duration_ms", 3, { logger: "api" }],
    ]);
  });
});
