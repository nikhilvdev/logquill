// The Node addon's API (bindings/node/index.d.ts), implemented over the C ABI with Deno's FFI, so the shared corpus
// harness (bindings/wasm/tests/hosts/harness.mjs, the same 22 groups the native addon, WASM and Python run) can check
// the C library without a per-group mapping of its own. Every stateless helper goes through `lq_call`; the chain uses its
// handle. Stateful plugin classes (sampling and the like) are not in the C ABI's first logger config, so they are absent.
const enc = new TextEncoder();

const file = { darwin: "liblogquill_ffi.dylib", windows: "logquill_ffi.dll" }[Deno.build.os] ?? "liblogquill_ffi.so";
const path = Deno.env.get("LQ_LIB") ?? new URL(`../../../../target/debug/${file}`, import.meta.url).pathname;
const lib = Deno.dlopen(path, {
  lq_abi_version: { parameters: [], result: "u32" },
  lq_string_free: { parameters: ["pointer"], result: "void" },
  lq_call: { parameters: ["buffer", "buffer", "buffer"], result: "pointer" },
  lq_chain_new: { parameters: ["buffer", "buffer"], result: "pointer" },
  lq_chain_stamp: { parameters: ["pointer", "buffer", "buffer"], result: "pointer" },
  lq_chain_head: { parameters: ["pointer", "buffer"], result: "pointer" },
  lq_chain_sign_head: { parameters: ["pointer", "buffer", "usize", "buffer"], result: "pointer" },
  lq_chain_free: { parameters: ["pointer"], result: "void" },
}).symbols;

if (lib.lq_abi_version() !== 1) throw new Error(`unexpected C ABI version ${lib.lq_abi_version()}`);

const cstr = (s) => enc.encode(`${s}\0`);

/** Reads and frees an owned C string; `error` is the out-parameter buffer the call wrote into. */
function result(pointer, error) {
  if (pointer === null) {
    const e = new BigUint64Array(error.buffer)[0];
    if (e === 0n) throw new Error("the call failed without a message");
    const message = Deno.UnsafePointerView.getCString(Deno.UnsafePointer.create(e));
    lib.lq_string_free(Deno.UnsafePointer.create(e));
    throw new Error(message);
  }
  const text = Deno.UnsafePointerView.getCString(pointer);
  lib.lq_string_free(pointer);
  return text;
}

const errorSlot = () => new Uint8Array(8);

/** `lq_call`: the result as JSON text. */
function raw(name, ...args) {
  const error = errorSlot();
  const out = lib.lq_call(cstr(name), cstr(JSON.stringify(args)), error);
  return result(out, error);
}
const value = (name, ...args) => JSON.parse(raw(name, ...args));
/** Helpers whose JavaScript API returns JSON *text*, not a parsed value: the raw result, unparsed. */
const text = (name, ...args) => raw(name, ...args);

const base64 = (bytes) => btoa(String.fromCharCode(...bytes));

// ---- constants ---------------------------------------------------------------------------------------------------------
export const GENESIS_HASH = value("genesisHash");
export const CYPHER = value("cypher");
export const LEAF_PLACEHOLDER = value("leafPlaceholder");
export const CAPTURE_ENV = value("captureEnv");

// ---- helpers that return a string --------------------------------------------------------------------------------------
export const canonicalJson = (json) => value("canonicalJson", json);
export const formatJson = (record) => value("formatJson", record);
export const formatText = (record) => value("formatText", record);
export const formatLogfmt = (record) => value("formatLogfmt", record);
export const createTableSql = (dialect, table) => value("createTableSql", dialect, table);
export const levelFromJson = (v) => value("levelFromJson", v);
export const formatTimestamp = (micros) => value("formatTimestamp", micros);
export const utcTimestamp = () => value("utcTimestamp");
export const spanIdFor = (v) => value("spanIdFor", v);
export const traceIdFromParent = (v) => value("traceIdFromParent", v);
export const signHead = (head, key) => value("signHead", head, base64(key));
export const verifyHeadSignature = (head, key, signature) => value("verifyHeadSignature", head, base64(key), signature);

// ---- helpers that return a string or nothing -----------------------------------------------------------------------------
export const parseTraceHeader = (h) => value("parseTraceHeader", h) ?? undefined;
export const traceIdFor = (meta) => value("traceIdFor", meta) ?? undefined;
export const epochNs = (timestamp) => value("epochNs", timestamp) ?? undefined;

// ---- helpers that return JSON text ------------------------------------------------------------------------------------------
export const parseLogfmt = (line) => text("parseLogfmt", line);
export const parseRecord = (record) => text("parseRecord", record);
export const applyPolicyValue = (v, policy) => text("applyPolicyValue", v, policy);
export const applyContentPolicy = (meta, policy) => text("applyContentPolicy", meta, policy);
export const toEcsDocument = (record) => text("toEcsDocument", record);
export const sqlRow = (record) => text("sqlRow", record);
export const graphRow = (record, runKey, traceKey) => text("graphRow", record, runKey ?? null, traceKey ?? null);
export const semconvResolve = (explicit) => text("semconvResolve", explicit ?? null);
export const logAttributes = (record, convention) => text("logAttributes", record, convention ?? null);
export const buildTrace = (records, runId) => text("buildTrace", records, runId);
export const exportSpec = (record, convention, provider, capture) => {
  const spec = text("exportSpec", record, convention ?? null, provider ?? null, capture ?? null);
  return spec === "null" ? null : spec;
};

// ---- helpers that return values ---------------------------------------------------------------------------------------------------
export const captureContentEnabled = (v) => value("captureContentEnabled", v ?? null);
export const contentFields = () => value("contentFields");
export const fieldClasses = () => value("fieldClasses");
export const keysInClass = (name) => value("keysInClass", name);
export const classifyRecord = (record) => value("classifyRecord", record);
export const verifyChain = (records, genesis) => value("verifyChain", records, genesis ?? null);
export const verifySignedChain = (records, key, signature, genesis) =>
  value("verifySignedChain", records, base64(key), signature, genesis ?? null);
export const buildLlmBlock = (fields) => {
  const built = value("buildLlmBlock", fields);
  return { block: built.block === null ? null : JSON.stringify(built.block), warnings: built.warnings };
};

// ---- classes ---------------------------------------------------------------------------------------------------------------------
export class KeyRedactor {
  #options;
  constructor(options) {
    // undefined fields are left out, as the Node addon's option object leaves them out
    this.#options = Object.fromEntries(Object.entries(options ?? {}).filter(([, v]) => v !== undefined));
  }
  redactMeta(meta) {
    return text("redactMeta", meta, this.#options);
  }
  redactLocal(name, textValue) {
    return value("redactLocal", name, textValue, this.#options);
  }
}

export class PiiRedactor {
  #replacement;
  constructor(replacement) {
    this.#replacement = replacement ?? null;
  }
  redactMeta(meta) {
    return text("piiRedactMeta", meta, this.#replacement);
  }
  redactText(textValue) {
    return value("piiRedactText", textValue, this.#replacement);
  }
}

export class Chain {
  #handle;
  constructor(genesis) {
    const error = errorSlot();
    this.#handle = lib.lq_chain_new(genesis === undefined || genesis === null ? null : cstr(genesis), error);
    if (this.#handle === null) result(null, error);
  }
  head() {
    const error = errorSlot();
    return result(lib.lq_chain_head(this.#handle, error), error);
  }
  signHead(key) {
    const error = errorSlot();
    return result(lib.lq_chain_sign_head(this.#handle, key, key.length, error), error);
  }
  stamp(record) {
    const error = errorSlot();
    return result(lib.lq_chain_stamp(this.#handle, cstr(record), error), error);
  }
  free() {
    lib.lq_chain_free(this.#handle);
  }
}
