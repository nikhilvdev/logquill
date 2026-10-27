import { compact, requireNative, type NativeTransport } from "../core/native.js";
import type { LogRecord } from "../core/records.js";
import { Transport } from "./transport.js";

// Rust-backed versions of sinks that also exist in JavaScript (`SyslogTransport`, `SQLiteTransport`,
// `DatadogTransport`, ...). A `Logger` hands records to them on the Rust worker thread, so the event loop
// never waits on a socket, a disk or a collector, and a logger whose transports are all native skips the
// SDK's queue. They need the native runtime: in the WebAssembly build, or with no addon, the constructors
// throw an error saying so, and the JavaScript transports are the ones to use.

/** Base for a transport implemented entirely in Rust. */
export class NativeSink extends Transport {
  constructor(handle: NativeTransport) {
    super();
    this.nativeHandle = handle;
  }

  /** The line the Rust formatter writes for `record`. */
  override format(record: LogRecord): string {
    return this.nativeHandle!.format(JSON.stringify(record));
  }

  /** Writes an already-formatted line now, on the calling thread (a `Logger` does not use this). */
  write(formatted: string, record: LogRecord): void {
    this.nativeHandle!.write(formatted, JSON.stringify(record));
  }

  /** Sends whatever the sink has buffered. */
  flush(): void {
    this.nativeHandle!.flush();
  }

  override close(): void {
    this.nativeHandle!.close();
  }
}

/** Batch bounds shared by the HTTP sinks. */
export interface BatchOptions {
  /** Send a batch once it holds this many records. Default 100. */
  maxRecords?: number;
  /** ...or this many bytes. Default 1,000,000. */
  maxBytes?: number;
  /** Seconds to wait for the endpoint. Default 10. */
  timeout?: number;
}

const addon = (feature: string) => requireNative(feature, "syslog");

/** Options for {@link RustDatadogTransport}. */
export interface RustDatadogOptions extends BatchOptions {
  /** Default `"datadoghq.com"`. */
  site?: string;
}

/** Datadog Logs, sent from Rust. */
export class RustDatadogTransport extends NativeSink {
  constructor(apiKey: string, options: RustDatadogOptions = {}) {
    const { site, ...batch } = options;
    super(addon("RustDatadogTransport").datadog(apiKey, site, compact(batch)));
  }
}

/** Options for {@link RustNewRelicTransport}. */
export interface RustNewRelicOptions extends BatchOptions {
  /** `"US"` (default) or `"EU"`. */
  region?: "US" | "EU";
}

/** New Relic Logs, sent from Rust (honours 429 `Retry-After`). */
export class RustNewRelicTransport extends NativeSink {
  constructor(licenseKey: string, options: RustNewRelicOptions = {}) {
    const { region, ...batch } = options;
    super(addon("RustNewRelicTransport").newRelic(licenseKey, region, compact(batch)));
  }
}

/** Options for {@link RustElasticsearchTransport}. */
export interface RustElasticsearchOptions extends BatchOptions {
  /** Default `"logs"`. */
  index?: string;
}

/** Elasticsearch bulk indexing of ECS documents, sent from Rust. */
export class RustElasticsearchTransport extends NativeSink {
  constructor(url: string, options: RustElasticsearchOptions = {}) {
    const { index, ...batch } = options;
    super(addon("RustElasticsearchTransport").elasticsearch(url, index, compact(batch)));
  }
}

/** Azure Application Insights, sent from Rust. */
export class RustAppInsightsTransport extends NativeSink {
  constructor(instrumentationKey: string, options: BatchOptions = {}) {
    super(addon("RustAppInsightsTransport").appInsights(instrumentationKey, compact(options)));
  }
}

/** Options for {@link RustSyslogTransport}. */
export interface RustSyslogOptions {
  /** Default `"localhost"`. */
  host?: string;
  /** Default 514. */
  port?: number;
  /** `"udp"` (default) or `"tcp"`. */
  protocol?: "udp" | "tcp";
  /** Syslog facility number. Default 1. */
  facility?: number;
  appName?: string;
  /** `"json"` (default), `"text"` or `"logfmt"`. */
  formatter?: "json" | "text" | "logfmt";
}

/** RFC 5424 syslog over UDP or TCP, from Rust. */
export class RustSyslogTransport extends NativeSink {
  constructor(options: RustSyslogOptions = {}) {
    super(addon("RustSyslogTransport").syslog(compact(options)));
  }
}

/** Options for {@link RustSQLiteTransport}. */
export interface RustSQLiteOptions {
  /** Default `":memory:"`. */
  filename?: string;
  /** Default `"logs"`; validated as an SQL identifier. */
  tableName?: string;
  /** Create the table if it is missing. For development; migrations are the application's job. */
  ensureSchema?: boolean;
  maxRecords?: number;
  maxBytes?: number;
}

/** Batched inserts into a SQLite database from Rust. The local viewer reads the same table. */
export class RustSQLiteTransport extends NativeSink {
  constructor(options: RustSQLiteOptions = {}) {
    super(addon("RustSQLiteTransport").sqlite(compact(options)));
  }
}

/** Options for the Rust OTLP sinks. */
export interface RustOTLPOptions extends BatchOptions {
  /** Default `http://localhost:4318/v1/traces` (or `/v1/logs`). */
  endpoint?: string;
  headers?: Record<string, string>;
  /** The `service.name` resource attribute. Default `"logquill"`. */
  serviceName?: string;
  /** `"latest"` or `"legacy"` GenAI attribute names. Unset reads `OTEL_SEMCONV_STABILITY_OPT_IN`. */
  semconv?: "latest" | "legacy";
  defaultProvider?: string;
  /** Add prompt and completion text (the logger's content policy still applies first). Default off. */
  captureContent?: boolean;
}

/** Spans (`invoke_agent`, `execute_tool`, `chat`) to an OTLP/HTTP traces endpoint, from Rust. */
export class RustOTLPTraceTransport extends NativeSink {
  constructor(options: RustOTLPOptions = {}) {
    super(addon("RustOTLPTraceTransport").otlpTraces(compact(options)));
  }
}

/** Every record as an OTLP log to an OTLP/HTTP logs endpoint, from Rust. */
export class RustOTLPLogsTransport extends NativeSink {
  constructor(options: RustOTLPOptions = {}) {
    super(addon("RustOTLPLogsTransport").otlpLogs(compact(options)));
  }
}
