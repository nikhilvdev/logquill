// The public face of the module: the Node addon's names and result shapes (`bindings/node/index.d.ts`), over
// the wasm-bindgen output in ../pkg. Nothing here imports a Node built-in; it has to load in a browser,
// Deno, Bun and a Worker.
//
//   import { init, canonicalJson } from "@logquill/wasm";
//   await init();                       // browsers: fetches the .wasm next to this file
//   await init(wasmModuleOrUrl);        // Workers pass the compiled module; bundlers pass a URL
//
// The Rust side takes options bags and returns structured results as JSON text (so number spellings and
// errors cross the boundary the same way they do in the Node addon); this file turns them into the option
// objects and plain objects the addon's callers already use.
import wasmInit, * as raw from "../pkg/logquill_wasm.js";

/** The hash a chain starts from, before its first record. Set once the module is loaded. */
export let GENESIS_HASH;
/** The key `graphRow` gives a record that has no `span_id` of its own. Set once the module is loaded. */
export let LEAF_PLACEHOLDER;
/** The Cypher statement every graph transport runs for a batch of rows. Set once the module is loaded. */
export let CYPHER;
/** The environment variable that turns on prompt and completion capture. Set once the module is loaded. */
export let CAPTURE_ENV;

function loaded() {
  GENESIS_HASH = raw.genesisHash();
  LEAF_PLACEHOLDER = raw.leafPlaceholder();
  CYPHER = raw.cypher();
  CAPTURE_ENV = raw.captureEnv();
}

/** Loads the module. `source` is a URL, a `Response`, bytes, or a `WebAssembly.Module`; omit it to fetch the file beside this one. */
export async function init(source) {
  await wasmInit(source === undefined ? undefined : { module_or_path: source });
  loaded();
}

/** Loads the module synchronously from bytes or a `WebAssembly.Module` (Node, Deno and Workers can; a page cannot fetch synchronously). */
export function initSync(source) {
  raw.initSync({ module: source });
  loaded();
}

const json = (options) => (options === undefined || options === null ? undefined : JSON.stringify(options));
const parse = JSON.parse;

// ---- pure helpers (same names as the Node addon) -----------------------------------------------------

export const {
  applyContentPolicy,
  applyPolicyValue,
  buildTrace,
  canonicalJson,
  captureContentEnabled,
  contentFields,
  createTableSql,
  epochNs,
  exportSpec,
  formatJson,
  formatLogfmt,
  formatText,
  formatTimestamp,
  graphRow,
  keysInClass,
  levelFromJson,
  logAttributes,
  parseLogfmt,
  parseRecord,
  parseTraceHeader,
  semconvResolve,
  signHead,
  spanIdFor,
  sqlRow,
  toEcsDocument,
  traceIdFor,
  traceIdFromParent,
  utcTimestamp,
  verifyHeadSignature,
} = raw;

/** Verifies a hash-chained sequence of records (JSON text of an array). */
export function verifyChain(records, genesis) {
  return parse(raw.verifyChainJson(records, genesis ?? undefined));
}

/** Verifies a chain (JSON text of an array) and that its head matches `signature`. */
export function verifySignedChain(records, key, signature, genesis) {
  return parse(raw.verifySignedChainJson(records, key, signature, genesis ?? undefined));
}

/** The well-known `meta` keys and their class, in registry order. */
export function fieldClasses() {
  return parse(raw.fieldClassesJson());
}

/** An `llm` block built from named fields (JSON text of an object), and what was wrong with the dropped ones. */
export function buildLlmBlock(fields) {
  return parse(raw.buildLlmBlockJson(fields));
}

/** What kind of exportable record this is: `{span, llm, tool}`. */
export function classifyRecord(record) {
  return parse(raw.classifyRecordJson(record));
}

// ---- classes (option objects in, plain objects out) ---------------------------------------------------

/** Redacts `meta` values by key, case-insensitively, at every depth. Options: `{keys?, classes?, replacement?}`. */
export class KeyRedactor {
  #r;
  constructor(options) {
    this.#r = new raw.KeyRedactor(json(options));
  }
  redactMeta(meta) {
    return this.#r.redactMeta(meta);
  }
  redactLocal(name, text) {
    return this.#r.redactLocal(name, text);
  }
}

/** Redacts PII- and secret-shaped text anywhere in `meta`. */
export class PiiRedactor {
  #r;
  constructor(replacement) {
    this.#r = new raw.PiiRedactor(replacement ?? undefined);
  }
  redactMeta(meta) {
    return this.#r.redactMeta(meta);
  }
  redactText(text) {
    return this.#r.redactText(text);
  }
}

/** A hash chain that several writers share. */
export class Chain {
  #r;
  constructor(genesis) {
    this.#r = new raw.Chain(genesis ?? undefined);
  }
  head() {
    return this.#r.head();
  }
  signHead(key) {
    return this.#r.signHead(key);
  }
  stamp(record) {
    return this.#r.stamp(record);
  }
}

/** Head-based sampling with tail elevation by trace. Options: `{rate, tail?, traceKey?, elevateAt?, ...}`. */
export class Sampling {
  #r;
  constructor(options) {
    this.#r = new raw.Sampling(json(options) ?? "{}");
  }
  decide(record, draw) {
    return parse(this.#r.decide(record, draw));
  }
  bufferedRecords() {
    return this.#r.bufferedRecords();
  }
  bufferedTraces() {
    return this.#r.bufferedTraces();
  }
  elevated() {
    return this.#r.elevated();
  }
}

/** Drops records once a stream exceeds `maxRecords` inside its own rolling window. */
export class RateLimit {
  #r;
  constructor(options) {
    this.#r = new raw.RateLimit(json(options) ?? "{}");
  }
  allow(record, now) {
    return this.#r.allow(record, now);
  }
  windows() {
    return this.#r.windows();
  }
}

/** Keeps errors and slow spans, samples the rest at a rate that follows demand, and caps bytes per window. */
export class AdaptiveSampling {
  #r;
  constructor(now, options) {
    this.#r = new raw.AdaptiveSampling(now, json(options));
  }
  decide(record, now, draw) {
    return this.#r.decide(record, now, draw);
  }
  get rate() {
    return this.#r.rate;
  }
  windowBytes() {
    return parse(this.#r.windowBytesJson());
  }
}

/** A bounded per-run ring buffer of low-level records that ships only if the run errors. */
export class FlightRecorder {
  #r;
  constructor(options) {
    this.#r = new raw.FlightRecorder(json(options));
  }
  decide(record) {
    return parse(this.#r.decide(record));
  }
  bufferedRecords() {
    return this.#r.bufferedRecords();
  }
  bufferedRuns() {
    return this.#r.bufferedRuns();
  }
  flushed() {
    return this.#r.flushed();
  }
}

/** One record per run on close, with total tokens, cost, tool calls, retries and errors. */
export class RunSummary {
  #r;
  constructor(options) {
    this.#r = new raw.RunSummary(json(options));
  }
  observe(record, timestamp) {
    return this.#r.observe(record, timestamp) ?? null;
  }
  openRuns() {
    return this.#r.openRuns();
  }
}

/** Stamps `meta.run_id` and a step counter on each record of a run. */
export class RunStamp {
  #r;
  constructor(runId, generated) {
    this.#r = new raw.RunStamp(runId ?? undefined, generated);
  }
  stamp(meta) {
    return this.#r.stamp(meta);
  }
  steps() {
    return this.#r.steps();
  }
  runId() {
    return this.#r.runId();
  }
}

/** Collapses repeats of one error inside a time window into a single alert with a count. */
export class AlertDedupe {
  #r;
  constructor(options) {
    this.#r = new raw.AlertDedupe(json(options));
  }
  observe(record, at) {
    return parse(this.#r.observe(record, at));
  }
  advance(at) {
    return parse(this.#r.advance(at));
  }
  close() {
    this.#r.close();
  }
  tracked() {
    return this.#r.tracked();
  }
}
