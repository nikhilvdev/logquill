import { levelName, parseLevel, type LevelInput } from "../core/levels.js";
import { compact, requireNative } from "../core/native.js";
import type { Plugin } from "../core/plugin.js";
import type { LogRecord } from "../core/records.js";
import type { Transport } from "../transports/transport.js";

// These plugins run the Rust core's state machines. A record crosses as JSON text, so each use costs a
// `JSON.stringify` and a parse inside the addon; leave them out of a pipeline that does not need them.

/** Writes records a plugin released (or made) straight to `transports`, skipping the rest of the pipeline. */
function writeAll(transports: Transport[] | undefined, texts: readonly string[], owner: string): void {
  if (transports === undefined) return;
  for (const text of texts) {
    const record = JSON.parse(text) as LogRecord;
    for (const transport of transports) {
      try {
        transport.write(transport.format(record), record);
      } catch (error) {
        console.error(`${owner}: ${transport.constructor.name} failed to write a log record`, error);
      }
    }
  }
}

/** Options for {@link AdaptiveSamplingPlugin}. */
export interface AdaptiveSamplingPluginOptions {
  /** Fraction of ordinary records kept when load is normal. Default 0.1. */
  baseRate?: number;
  /** The rate never goes below this. Default 0.01. */
  minRate?: number;
  /** The rate never goes above this. Default 1. */
  maxRate?: number;
  /** A record at or above this level is always kept. Default `ERROR`. */
  keepAt?: LevelInput;
  /** A span that took at least this long (`meta.duration_ms`) is always kept. Default 1000. */
  slowMs?: number;
  /** Output cap: records that would push a window past this many bytes are dropped. Default 100,000. */
  maxBytesPerSecond?: number;
  /** Length of the window the byte cap and the rate are measured over, in seconds. Default 1. */
  windowSeconds?: number;
  /** Clock, in seconds. Defaults to the wall clock; override in tests. */
  now?: () => number;
  /** Source of randomness in `[0, 1)`. Defaults to `Math.random`; override in tests. */
  rng?: () => number;
}

/**
 * Keeps errors and slow spans, samples the rest at a rate that follows demand, and caps the bytes
 * written per window (Rust core; see `AdaptiveSampling`). The rate falls under load and recovers when it
 * eases.
 */
export class AdaptiveSamplingPlugin implements Plugin {
  private readonly sampler: InstanceType<ReturnType<typeof requireNative>["AdaptiveSampling"]>;
  private readonly now: () => number;
  private readonly rng: () => number;

  constructor(options: AdaptiveSamplingPluginOptions = {}) {
    this.now = options.now ?? (() => Date.now() / 1000);
    this.rng = options.rng ?? Math.random;
    this.sampler = new (requireNative("AdaptiveSamplingPlugin").AdaptiveSampling)(
      this.now(),
      compact({
        baseRate: options.baseRate,
        minRate: options.minRate,
        maxRate: options.maxRate,
        keepAt: options.keepAt === undefined ? undefined : levelName(parseLevel(options.keepAt)),
        slowMs: options.slowMs,
        maxBytesPerSecond: options.maxBytesPerSecond,
        windowSeconds: options.windowSeconds,
      }),
    );
  }

  /** The sample rate in force now. */
  get rate(): number {
    return this.sampler.rate;
  }

  beforeLog(record: LogRecord): LogRecord | null {
    return this.sampler.decide(JSON.stringify(record), this.now(), this.rng()) ? record : null;
  }
}

/** Options for {@link FlightRecorderPlugin}. */
export interface FlightRecorderPluginOptions {
  /** `meta` key that groups records into a run. Default `"run_id"`. */
  runKey?: string;
  /** Records at or above this level pass straight through. Lower ones are held back. Default `INFO`. */
  shipAt?: LevelInput;
  /** A record at or above this level ships its run's held-back records. Default `ERROR`. */
  flushAt?: LevelInput;
  /** Held-back records kept across every run before the oldest run is dropped. Default 1000. */
  maxBufferedRecords?: number;
  /** Runs held at once. Default 200. */
  maxRuns?: number;
  /** Runs remembered as already shipped. Default 10,000. */
  maxFlushed?: number;
  /** Where released records are written: the same array given to the `Logger`. Without it nothing is released. */
  transports?: Transport[];
}

/**
 * Holds a run's low-level records (DEBUG and TRACE by default) in a bounded buffer and ships them only if
 * the run later errors, so a failing run arrives with its debug context and a passing one costs nothing.
 * Strictly memory-capped (Rust core; see `FlightRecorder`).
 */
export class FlightRecorderPlugin implements Plugin {
  private readonly recorder: InstanceType<ReturnType<typeof requireNative>["FlightRecorder"]>;
  private readonly transports: Transport[] | undefined;

  constructor(options: FlightRecorderPluginOptions = {}) {
    this.transports = options.transports;
    this.recorder = new (requireNative("FlightRecorderPlugin").FlightRecorder)(
      compact({
        runKey: options.runKey,
        shipAt: options.shipAt === undefined ? undefined : levelName(parseLevel(options.shipAt)),
        flushAt: options.flushAt === undefined ? undefined : levelName(parseLevel(options.flushAt)),
        maxBufferedRecords: options.maxBufferedRecords,
        maxRuns: options.maxRuns,
        maxFlushed: options.maxFlushed,
      }),
    );
  }

  /** How many records are held back waiting on their run. */
  get bufferedRecords(): number {
    return this.recorder.bufferedRecords();
  }

  beforeLog(record: LogRecord): LogRecord | null {
    const decision = this.recorder.decide(JSON.stringify(record));
    writeAll(this.transports, decision.released, "FlightRecorderPlugin");
    return decision.kept ? record : null;
  }
}

/** Options for {@link RunSummaryPlugin}. */
export interface RunSummaryPluginOptions {
  /** `meta` key that groups records into a run. Default `"run_id"`. */
  runKey?: string;
  /** `logger` of the summary record. Default `"app.run_summary"`. */
  loggerName?: string;
  /** Runs tracked at once before the oldest is dropped. Default 1000. */
  maxRuns?: number;
  /** Where the summary record is written: the same array given to the `Logger`. */
  transports: Transport[];
}

/**
 * Totals a run's tokens, cost, tool calls, retries and errors, and writes one summary record when the
 * run's root span closes (Rust core; see `RunSummary`). It feeds the local viewer and dashboards.
 */
export class RunSummaryPlugin implements Plugin {
  private readonly summary: InstanceType<ReturnType<typeof requireNative>["RunSummary"]>;
  private readonly transports: Transport[];
  private readonly stamp: () => string;

  constructor(options: RunSummaryPluginOptions) {
    const addon = requireNative("RunSummaryPlugin");
    this.transports = options.transports;
    this.stamp = () => addon.utcTimestamp();
    this.summary = new addon.RunSummary(
      compact({ runKey: options.runKey, loggerName: options.loggerName, maxRuns: options.maxRuns }),
    );
  }

  afterLog(record: LogRecord): void {
    const summary = this.summary.observe(JSON.stringify(record), this.stamp());
    if (summary !== null && summary !== undefined) {
      writeAll(this.transports, [summary], "RunSummaryPlugin");
    }
  }
}
