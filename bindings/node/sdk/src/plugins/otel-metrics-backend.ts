import { createRequire } from "node:module";
import type { MetricsBackend } from "./metrics-plugin.js";

/** The part of an OpenTelemetry `Meter` this backend uses; the real one from `@opentelemetry/api` fits. */
export interface MeterLike {
  createCounter(name: string): { add(value: number, attributes?: Record<string, string>): void };
  createHistogram(name: string): { record(value: number, attributes?: Record<string, string>): void };
}

/** Options for {@link OTelMetricsBackend}. */
export interface OTelMetricsBackendOptions {
  /**
   * The meter to record on, e.g. `metrics.getMeter("my-service")` from `@opentelemetry/api`. Omit it to use the
   * global meter provider's `"logquill"` meter, which needs the optional `@opentelemetry/api` package.
   */
  meter?: MeterLike;
}

/** Distinct metric names remembered; a name beyond this is not recorded (the cache is bounded). */
const MAX_INSTRUMENTS = 1000;

/**
 * Sends `MetricsPlugin`'s counters and histograms through the OpenTelemetry Metrics API, so they reach whatever
 * exporter the application has configured (OTLP, Prometheus, ...). With no meter provider registered the API's
 * no-op meter takes them, which costs nothing.
 */
export class OTelMetricsBackend implements MetricsBackend {
  private readonly meter: MeterLike;
  private readonly counters = new Map<string, { add(value: number, attributes?: Record<string, string>): void }>();
  private readonly histograms = new Map<string, { record(value: number, attributes?: Record<string, string>): void }>();

  constructor(options: OTelMetricsBackendOptions = {}) {
    this.meter = options.meter ?? OTelMetricsBackend.globalMeter();
  }

  private static globalMeter(): MeterLike {
    try {
      const require = createRequire(import.meta.url);
      const api = require("@opentelemetry/api") as { metrics: { getMeter(name: string): MeterLike } };
      return api.metrics.getMeter("logquill");
    } catch (cause) {
      throw new Error(
        "OTelMetricsBackend needs the optional `@opentelemetry/api` package — install it with `npm install @opentelemetry/api`, or pass a `meter`",
        { cause },
      );
    }
  }

  increment(name: string, tags: Record<string, string>): void {
    let counter = this.counters.get(name);
    if (counter === undefined) {
      if (this.counters.size >= MAX_INSTRUMENTS) return;
      counter = this.meter.createCounter(name);
      this.counters.set(name, counter);
    }
    counter.add(1, tags);
  }

  observe(name: string, value: number, tags: Record<string, string>): void {
    let histogram = this.histograms.get(name);
    if (histogram === undefined) {
      if (this.histograms.size >= MAX_INSTRUMENTS) return;
      histogram = this.meter.createHistogram(name);
      this.histograms.set(name, histogram);
    }
    histogram.record(value, tags);
  }
}
