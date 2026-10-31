// Parity acceptance, condition 3 (spec 09, section D): the public API exposes every name `docs/api-names.md`
// lists for Node / TypeScript. The lists below are copied from that document; a name missing here is a
// real gap, and a name the document marks "where the library exists" or "browser only" is left out.
import { describe, expect, it } from "vitest";
import * as sdk from "../../src/index.js";

const exported = sdk as unknown as Record<string, unknown>;

const loggerMethods = [
  "trace", "debug", "info", "warn", "error", "fatal", // levels
  "thought", "action", "observation", "decision", // agent kinds
  "llmCall", "span", "child", "use", "setLevel", "flush", "close", "opt",
];

const functions = [
  "bindContext", "currentContext", "disable", "enable", "parseRecord", "parseLogfmt", "parse",
  "loggerFromFile", "loggerFromEnv", "loadConfig",
  "verifyChain", "signHead", "verifySignedChain",
  "withLambda", "installShutdownHandlers", "installSignalLevelHandler",
];

const classes = [
  // plugins
  "ContextPlugin", "RunPlugin", "RateLimitPlugin", "SamplingPlugin", "AdaptiveSamplingPlugin",
  "FlightRecorderPlugin", "RunSummaryPlugin", "TraceContextPlugin", "RedactPlugin", "PIIRedactPlugin",
  "TamperEvidentPlugin", "AlertingPlugin", "SlackAlertPlugin", "PagerDutyAlertPlugin", "EmailAlertPlugin",
  "MetricsPlugin", "OtelSpanProcessor", "AuditLogger",
  // transports
  "ConsoleTransport", "FileTransport", "HTTPTransport", "SyslogTransport", "DatadogTransport",
  "NewRelicTransport", "ElasticsearchTransport", "AppInsightsTransport", "OTLPTransport",
  "OTelLogsTransport", "SQLiteTransport", "PostgresTransport", "MySQLTransport", "MongoDBTransport",
  "RedisTransport", "DynamoDBTransport", "KafkaTransport", "RabbitMQTransport", "SQSTransport",
  "PubSubTransport", "CloudWatchTransport", "CloudLoggingTransport", "Neo4jTransport",
  "FalkorDBTransport", "NeptuneTransport", "CollectingTransport",
  // runtime
  "LevelFileWatcher", "LevelEnvWatcher",
];

describe("docs/api-names.md, Node / TypeScript column", () => {
  it("Logger has every method", () => {
    const prototype = sdk.Logger.prototype as unknown as Record<string, unknown>;
    expect(loggerMethods.filter((name) => typeof prototype[name] !== "function")).toEqual([]);
  });

  it("the module exports every function", () => {
    expect(functions.filter((name) => typeof exported[name] !== "function")).toEqual([]);
  });

  it("the module exports every plugin, transport and watcher class", () => {
    expect(classes.filter((name) => typeof exported[name] !== "function")).toEqual([]);
  });

  it("has the integration helpers: SDK patching and MCP propagation (logquill/instrument), the logging bridges", async () => {
    const instrument = (await import("../../src/instrument.js")) as unknown as Record<string, unknown>;
    // `instrument.anthropic` / `instrument.openai` are Instrumenters (callable objects), not plain functions
    for (const name of ["anthropic", "openai"]) expect(instrument[name], name).toBeDefined();
    for (const name of ["propagate", "inbound"]) expect(typeof instrument[name], name).toBe("function");
    // pino and winston are the bridges the table lists for Node
    expect(typeof exported.LogQuillPinoDestination).toBe("function");
    const winston = (await import("../../src/winston.js")) as unknown as Record<string, unknown>;
    expect(typeof winston.LogQuillWinstonTransport).toBe("function");
  });
});
