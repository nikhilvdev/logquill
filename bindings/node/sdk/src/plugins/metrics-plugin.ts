import { Level, parseLevel, type LevelInput } from "../core/levels.js";
import { compact, requireNative } from "../core/native.js";
import type { Plugin } from "../core/plugin.js";
import type { LogRecord } from "../core/records.js";

/** Where {@link MetricsPlugin} sends numbers. Implement it to feed a system the built-in backends do not cover. */
export interface MetricsBackend {
  /** Adds one to the counter `name`. */
  increment(name: string, tags: Record<string, string>): void;
  /** Records one value in the histogram `name`. */
  observe(name: string, value: number, tags: Record<string, string>): void;
}

/** Options for {@link StatsDBackend}. */
export interface StatsDBackendOptions {
  /** Default `"localhost"`. */
  host?: string;
  /** Default 8125. */
  port?: number;
  /** Put in front of every metric name. */
  prefix?: string;
}

/** StatsD over UDP, fire and forget (Rust): `name:1|c` and `name:value|h`, with `|#key:value` tags. */
export class StatsDBackend implements MetricsBackend {
  private readonly native: InstanceType<ReturnType<typeof requireNative>["StatsdBackend"]>;

  constructor(options: StatsDBackendOptions = {}) {
    this.native = new (requireNative("StatsDBackend", "StatsdBackend").StatsdBackend)(
      options.host,
      options.port,
      options.prefix,
    );
  }

  increment(name: string, tags: Record<string, string>): void {
    this.native.increment(name, tags);
  }

  observe(name: string, value: number, tags: Record<string, string>): void {
    this.native.observe(name, value, tags);
  }
}

/** Options for {@link PrometheusPushgatewayBackend}. */
export interface PrometheusPushgatewayBackendOptions {
  /** Base URL of the pushgateway, e.g. `http://pushgateway:9091`. */
  gatewayUrl: string;
  /** The `job` label. Default `"logquill"`. */
  jobName?: string;
  /** Seconds to wait for the gateway. Default 5. */
  timeout?: number;
  /** Distinct series kept before new ones are dropped. Default 1000. */
  maxSeries?: number;
}

/**
 * Accumulates counters and histogram sum and count in memory, bounded to `maxSeries`, and PUTs them to a
 * Prometheus pushgateway on {@link flush} (Rust).
 */
export class PrometheusPushgatewayBackend implements MetricsBackend {
  private readonly native: InstanceType<ReturnType<typeof requireNative>["PushgatewayBackend"]>;

  constructor(options: PrometheusPushgatewayBackendOptions) {
    this.native = new (requireNative("PrometheusPushgatewayBackend", "PushgatewayBackend").PushgatewayBackend)({
      gatewayUrl: options.gatewayUrl,
      ...compact({ jobName: options.jobName, timeout: options.timeout, maxSeries: options.maxSeries }),
    });
  }

  increment(name: string, tags: Record<string, string>): void {
    this.native.increment(name, tags);
  }

  observe(name: string, value: number, tags: Record<string, string>): void {
    this.native.observe(name, value, tags);
  }

  /** Sends every series accumulated so far. Throws if the gateway refuses. */
  flush(): void {
    this.native.flush();
  }

  /** Samples dropped because `maxSeries` distinct series were already held. */
  get droppedSamples(): number {
    return this.native.droppedSamples();
  }

  /** Sends what is accumulated, once. */
  close(): void {
    this.flush();
  }
}

/** Options for {@link MetricsPlugin}. */
export interface MetricsPluginOptions {
  /** Counter incremented for every record at `errorAt` or above. Default `"logquill.errors"`. */
  errorMetric?: string;
  /** Histogram fed by every record that carries `meta.duration_ms`. Default `"logquill.duration_ms"`. */
  durationMetric?: string;
  /** Records at or above this level count as errors. Default `ERROR`. */
  errorAt?: LevelInput;
}

/**
 * Turns log records into metrics: every record at `errorAt` or above increments a counter, and every
 * record carrying `meta.duration_ms` (each span-closing record) feeds a histogram, both tagged by
 * `logger`. Runs in `afterLog`, after the record has gone to every transport, so a slow backend never
 * delays the record it is reporting on.
 */
export class MetricsPlugin implements Plugin {
  private readonly backend: MetricsBackend;
  private readonly errorMetric: string;
  private readonly durationMetric: string;
  private readonly errorAt: Level;

  constructor(backend: MetricsBackend, options: MetricsPluginOptions = {}) {
    this.backend = backend;
    this.errorMetric = options.errorMetric ?? "logquill.errors";
    this.durationMetric = options.durationMetric ?? "logquill.duration_ms";
    this.errorAt = parseLevel(options.errorAt ?? Level.ERROR);
  }

  afterLog(record: LogRecord): void {
    const tags = { logger: record.logger };
    if (parseLevel(record.level) >= this.errorAt) {
      this.backend.increment(this.errorMetric, tags);
    }
    const duration = record.meta.duration_ms;
    if (typeof duration === "number" && Number.isFinite(duration)) {
      this.backend.observe(this.durationMetric, duration, tags);
    }
  }

  /** Lets the backend send what it holds (the pushgateway backend). Call it at shutdown. */
  close(): void {
    (this.backend as { close?: () => void }).close?.();
  }
}
