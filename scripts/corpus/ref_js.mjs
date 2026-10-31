// Runs the JavaScript reference (a checkout of logquill-js) on one corpus inputs file and
// prints one output per case, in the same shape as ref_py.py.
//
// Usage: LOGQUILL_JS_DIR=<logquill-js checkout> node ref_js.mjs inputs/<group>.json
//
// Sources are bundled with that checkout's esbuild so unexported helpers can be exercised.
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const jsDir = process.env.LOGQUILL_JS_DIR;
const esbuild = createRequire(join(jsDir, "package.json"))("esbuild");
const work = mkdtempSync(join(tmpdir(), "logquill-ref-js-"));
const entry = join(work, "entry.ts");
const src = (name) => JSON.stringify(join(jsDir, "src", name));
writeFileSync(
  entry,
  `export { canonicalStringify } from ${src("core/canonical-json.ts")};
export { applyContentPolicy, parseContentPolicy, FIELD_CLASSES, CONTENT_FIELDS } from ${src("core/privacy.ts")};
export { parseLevel, levelName } from ${src("core/levels.ts")};
export { createRecord, parseRecord, buildLlmBlock, utcTimestamp } from ${src("core/records.ts")};
export { parseTraceHeader } from ${src("plugins/trace-context-plugin.ts")};
export { spanIdFor, traceIdFor, traceIdFromParent } from ${src("otel/ids.ts")};
export { RedactPlugin, DEFAULT_REDACTED_KEYS } from ${src("plugins/redact-plugin.ts")};
export { PIIRedactPlugin } from ${src("plugins/pii-redact-plugin.ts")};
export { JSONFormatter } from ${src("core/formatter.ts")};
export { TextFormatter } from ${src("core/text-formatter.ts")};
export { LogfmtFormatter, parseLogfmt } from ${src("core/logfmt-formatter.ts")};
export * as semconv from ${src("otel/semconv.ts")};
export { toEcsDocument } from ${src("transports/cloud/ecs-mapper.ts")};
export { SQLiteTransport } from ${src("transports/sql/sqlite-transport.ts")};
export { PostgresTransport } from ${src("transports/sql/postgres-transport.ts")};
export { MySQLTransport } from ${src("transports/sql/mysql-transport.ts")};
export { SamplingPlugin } from ${src("plugins/sampling-plugin.ts")};
export { RateLimitPlugin } from ${src("plugins/rate-limit-plugin.ts")};
export { RunPlugin } from ${src("plugins/run-plugin.ts")};
export { TamperEvidentPlugin, GENESIS_HASH } from ${src("plugins/tamper-evident-plugin.ts")};
`,
);
const bundle = join(work, "bundle.mjs");
esbuild.buildSync({ entryPoints: [entry], bundle: true, platform: "node", format: "esm", outfile: bundle, logLevel: "error" });
const lib = await import(pathToFileURL(bundle).href);
const { createHash, createHmac } = await import("node:crypto");

const sha = (text) => createHash("sha256").update(text).digest("hex");
const clone = (value) => JSON.parse(JSON.stringify(value));

// JSON.parse refuses the non-finite words Python's json accepts, so swap them for sentinels
// outside strings and put the numbers back afterwards.
function parseWithNonFinite(text) {
  const swapped = text
    .split(/("(?:[^"\\]|\\.)*")/)
    .map((part, i) => (i % 2 ? part : part.replace(/-?Infinity|NaN/g, (w) => `"@@nf@@${w}"`)))
    .join("");
  return JSON.parse(swapped, (_key, value) =>
    typeof value === "string" && value.startsWith("@@nf@@") ? Number(value.slice(6)) : value,
  );
}

function canonicalJson(c) {
  const text = lib.canonicalStringify(parseWithNonFinite(c.json));
  return { text, sha256: sha(text) };
}

function mutate(chain, mutations) {
  let out = clone(chain);
  for (const s of mutations) {
    if (s.type === "edit_message") out[s.index].message = s.value;
    else if (s.type === "edit_meta") out[s.index].meta[s.key] = s.value;
    else if (s.type === "edit_level") out[s.index].level = s.value;
    else if (s.type === "edit_llm") out[s.index].llm[s.key] = s.value;
    else if (s.type === "edit_schema_version") out[s.index].schema_version = s.value;
    else if (s.type === "edit_hash") out[s.index].meta.hash = s.value;
    else if (s.type === "edit_prev_hash") out[s.index].meta.prev_hash = s.value;
    else if (s.type === "drop_hash") delete out[s.index].meta.hash;
    else if (s.type === "delete") out.splice(s.index, 1);
    else if (s.type === "swap") [out[s.index], out[s.other]] = [out[s.other], out[s.index]];
    else if (s.type === "truncate") out = out.slice(0, s.keep);
    else throw new Error(s.type);
  }
  return out;
}

function hashChain(c) {
  const genesis = c.genesis ?? lib.GENESIS_HASH;
  const plugin = new lib.TamperEvidentPlugin({ genesisHash: genesis });
  const chain = c.records.map((r) => plugin.beforeLog(clone(r)));
  const out = { chain };
  out.head_hash = chain.length ? chain[chain.length - 1].meta.hash : genesis;
  if (c.key != null) out.signature = createHmac("sha256", c.key).update(out.head_hash).digest("hex");
  const mutated = mutate(chain, c.mutations ?? []);
  out.verify = { ok: lib.TamperEvidentPlugin.verifyChain(mutated, { genesisHash: genesis }) };
  return out;
}

function contentPolicy(c) {
  if ("error_policy" in c) {
    try {
      lib.parseContentPolicy(c.error_policy);
    } catch (error) {
      return { error: error.message };
    }
    return { ok: true };
  }
  const field = c.field ?? "input_messages";
  const meta = { [field]: JSON.parse(c.json) };
  lib.applyContentPolicy(meta, c.policy);
  return { value: meta[field] };
}

function fieldClasses() {
  const registry = { ...lib.FIELD_CLASSES };
  const inClass = (name) => Object.keys(registry).filter((k) => registry[k] === name).sort();
  return {
    registry,
    content_fields: [...lib.CONTENT_FIELDS],
    keys_in_class: { secret: inClass("secret"), pii: inClass("pii"), content: inClass("content") },
  };
}

function levels(c) {
  try {
    return { level: lib.levelName(lib.parseLevel(c.value)) };
  } catch (error) {
    return { error: error.message };
  }
}

const WIRE = {
  model: "model", tokens_in: "tokensIn", tokens_out: "tokensOut",
  cost_usd: "costUsd", latency_ms: "latencyMs", finish_reason: "finishReason",
};

function recordsCase(c) {
  if (c.op === "timestamp") {
    const real = Date.now;
    Date.now = () => Math.floor(c.unix_micros / 1000);
    const RealDate = Date;
    try {
      // utcTimestamp reads the clock through `new Date()`, so fix that too.
      globalThis.Date = class extends RealDate {
        constructor(...args) { super(...(args.length ? args : [Math.floor(c.unix_micros / 1000)])); }
      };
      globalThis.Date.now = () => Math.floor(c.unix_micros / 1000);
      return { timestamp: lib.utcTimestamp() };
    } finally {
      globalThis.Date = RealDate;
      Date.now = real;
    }
  }
  if (c.op === "llm_block") {
    const warnings = [];
    const warn = console.warn;
    console.warn = (...parts) => warnings.push(parts.join(" "));
    try {
      const fields = {};
      for (const [name, value] of Object.entries(c.fields)) fields[WIRE[name] ?? name] = value;
      const block = lib.buildLlmBlock(fields);
      return { block: block ?? null, warnings };
    } finally {
      console.warn = warn;
    }
  }
  if (c.op === "parse_record") {
    try {
      return { record: lib.parseRecord(c.record) };
    } catch (error) {
      return { error: error.message };
    }
  }
  throw new Error(c.op);
}

function traceHeaders(c) {
  return { trace_id: lib.parseTraceHeader(c.header) ?? null };
}

function otelIds(c) {
  if (c.op === "span_id") return { id: lib.spanIdFor(c.value) };
  if (c.op === "trace_id") return { id: lib.traceIdFor(c.meta) ?? null };
  return { id: lib.traceIdFromParent(c.value) };
}

function redaction(c) {
  const replacement = c.replacement ?? "***";
  try {
    if (c.op === "redact") {
      const keys = new Set(c.keys ?? lib.DEFAULT_REDACTED_KEYS);
      for (const name of c.classes ?? []) {
        for (const [key, cls] of Object.entries(lib.FIELD_CLASSES)) if (cls === name) keys.add(key);
      }
      const plugin = new lib.RedactPlugin({ keys: [...keys], replacement });
      return { meta: plugin.beforeLog({ meta: clone(c.meta) }).meta };
    }
    if (c.op === "pii") {
      const plugin = new lib.PIIRedactPlugin({ replacement });
      return { meta: plugin.beforeLog({ meta: clone(c.meta) }).meta };
    }
  } catch (error) {
    return { error: `${error.name}: ${error.message}` };
  }
  return { unsupported: "no redact_local in the JavaScript package" };
}

function formatters(c) {
  if (c.op === "parse_logfmt") return { fields: lib.parseLogfmt(c.line) };
  const record = "record_json" in c ? parseWithNonFinite(c.record_json) : c.record;
  const formatter = { json: lib.JSONFormatter, text: lib.TextFormatter, logfmt: lib.LogfmtFormatter }[c.op];
  try {
    return { line: new formatter().format(record) };
  } catch (error) {
    return { error: `${error.name}: ${error.message}` };
  }
}

function ecs(c) {
  try {
    return { document: lib.toEcsDocument(clone(c.record)) };
  } catch (error) {
    return { error: `${error.name}: ${error.message}` };
  }
}

function specObject(spec) {
  return {
    name: spec.name, kind: spec.kind, attributes: spec.attributes,
    duration_ms: spec.durationMs ?? null, is_error: spec.isError,
    events: spec.events.map(([name, attrs]) => [name, attrs]),
  };
}

function semconvCase(c) {
  const sc = lib.semconv;
  try {
    if (c.op === "resolve") {
      const found = sc.resolveConvention(c.explicit ?? undefined, c.env ?? {});
      return { version: found.version, names: { ...found.names } };
    }
    if (c.op === "capture") return { enabled: sc.captureContentEnabled(c.env) };
    if (c.op === "epoch") {
      const ms = Date.parse(c.timestamp);
      if (Number.isNaN(ms)) return { ns: null };
      const [seconds, nanos] = sc.epochHr(c.timestamp);
      return { ns: Number(BigInt(seconds) * 1000000000n + BigInt(nanos)) };
    }
    const record = clone(c.record);
    if (c.op === "classify") {
      return { span: sc.isSpanRecord(record), llm: sc.isLlmRecord(record), tool: sc.isToolRecord(record) };
    }
    const convention = sc.resolveConvention(c.convention ?? "latest", {});
    if (c.op === "log_attributes") return { attributes: sc.logAttributes(record, convention) };
    const defaultProvider = c.default_provider ?? "unknown";
    if (sc.isLlmRecord(record)) {
      return { span: specObject(sc.llmSpec(record, convention, { defaultProvider, captureContent: c.capture_content ?? false })) };
    }
    if (sc.isToolRecord(record)) return { span: specObject(sc.toolSpec(record, convention)) };
    if (sc.isSpanRecord(record)) return { span: specObject(sc.spanSpec(record, convention, { defaultProvider })) };
    return { span: null };
  } catch (error) {
    return { error: `${error.name}: ${error.message}` };
  }
}

function sqlRows(c) {
  const classes = { sqlite: lib.SQLiteTransport, postgres: lib.PostgresTransport, mysql: lib.MySQLTransport };
  try {
    const transport = new classes[c.dialect ?? "sqlite"]({ client: {}, tableName: c.table_name ?? "logs" });
    if (c.op === "ddl") return { sql: transport.createTableSQL() };
    return { row: transport.toItem("", clone(c.record)) };
  } catch (error) {
    return { error: `${error.name}: ${error.message}` };
  }
}

function graphRows() {
  return { unsupported: "no graph transport in the JavaScript package" };
}

const KEYS = {
  logger_level: undefined, logger: (r) => r.logger, message: (r) => r.message,
};

function scenarioJs(group, c) {
  const state = { clock: c.start ?? 0, rng: 0 };
  const written = [];
  const transport = { format: (r) => r.message, write: (_f, r) => written.push(clone(r)) };
  const opts = c.options ?? {};
  let plugin;
  try {
    if (group === "sampling") {
      plugin = new lib.SamplingPlugin(opts.rate, {
        rng: () => state.rng, traceKey: opts.trace_key, elevateAt: opts.elevate_at,
        transports: opts.tail ? [transport] : undefined,
        maxBufferedRecords: opts.max_buffered_records, maxTraces: opts.max_traces,
      });
    } else if (group === "rate-limit") {
      plugin = new lib.RateLimitPlugin(opts.max_records, opts.per_seconds, {
        clock: () => state.clock, maxKeys: opts.max_keys, keyFunc: KEYS[opts.key ?? "logger_level"],
      });
    } else {
      plugin = new lib.RunPlugin({ runId: opts.run_id });
    }
  } catch (error) {
    return { error: error.message };
  }
  const steps = c.steps.map((step) => {
    state.clock = step.clock ?? state.clock;
    state.rng = step.rng ?? state.rng;
    written.length = 0;
    const result = plugin.beforeLog(clone(step.record));
    const out = { kept: result !== null && result !== undefined };
    if (group === "run-plugin" && out.kept) {
      out.meta = { ...result.meta };
      if (opts.run_id === undefined && out.meta.run_id === plugin.runId) out.meta.run_id = "<generated>";
    }
    out.written = written.map((w) => w.message);
    return out;
  });
  return { steps };
}

const GROUPS = {
  sampling: (c) => scenarioJs("sampling", c),
  "rate-limit": (c) => scenarioJs("rate-limit", c),
  "run-plugin": (c) => scenarioJs("run-plugin", c),
  "adaptive-sampling": () => ({ unsupported: "no adaptive sampling in the JavaScript package" }),
  "flight-recorder": () => ({ unsupported: "no flight recorder in the JavaScript package" }),
  "run-summary": () => ({ unsupported: "no run summary in the JavaScript package" }),
  "alert-dedupe": () => ({ unsupported: "alert deduplication is not exercised with a fake clock in the JavaScript package" }),
  "trace-tree": () => ({ unsupported: "no trace tree in the JavaScript package" }),
  "sql-rows": sqlRows, "graph-rows": graphRows,
  ecs, semconv: semconvCase,
  formatters,
  redaction,
  "trace-headers": traceHeaders, "otel-ids": otelIds,
  "canonical-json": canonicalJson, "hash-chain": hashChain, "content-policy": contentPolicy,
  "field-classes": fieldClasses, levels, records: recordsCase,
};

const spec = JSON.parse(readFileSync(process.argv[2], "utf8"));
process.stdout.write(JSON.stringify(spec.cases.map((c) => GROUPS[spec.group](c.input))));
