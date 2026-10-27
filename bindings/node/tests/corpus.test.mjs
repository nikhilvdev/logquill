// The shared corpus (schema/corpus/*.json), group by group, against the native addon. The mapping
// from each case's `input` to a call and back to `expected` mirrors the Rust reference harness
// (crates/logquill-core/tests/{processing,integrity}.rs).
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import * as q from "../index.js";
import { LOSSLESS, parseLossless, plain, readLossless, SCHEMA, toText } from "./lossless.mjs";

const skip = LOSSLESS ? false : "needs Node 21+ (JSON.parse source text access)";
const cases = (group) => readLossless(`${SCHEMA}corpus/${group}.json`).cases;
const text = (value) => toText(value);
const camel = (key) => key.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
const camelKeys = (object) =>
  Object.fromEntries(Object.entries(object ?? {}).map(([k, v]) => [camel(k), v]));

/** Runs every case of `group` through `run` and compares the result with `expected`. */
function group(name, run) {
  describe(`corpus: ${name}`, { skip }, () => {
    for (const { name: label, input, expected } of cases(name)) {
      it(label, () => {
        assert.deepEqual(plain(run(input)), plain(expected));
      });
    }
  });
}

const parse = (json) => JSON.parse(json);
const number = (value, fallback) => (value === undefined ? fallback : Number(plain(value)));

group("redaction", (input) => {
  const replacement = input.replacement;
  const meta = text(input.meta ?? {});
  if (input.op.startsWith("redact")) {
    const redactor = new q.KeyRedactor({
      keys: input.keys,
      classes: input.classes,
      replacement,
    });
    if (input.op === "redact_local") return { text: redactor.redactLocal(input.name, input.text) };
    return { meta: parse(redactor.redactMeta(meta)) };
  }
  const redactor = new q.PiiRedactor(replacement);
  if (input.op === "pii_local") return { text: redactor.redactText(input.text) };
  return { meta: parse(redactor.redactMeta(meta)) };
});

group("ecs", (input) => ({ document: parse(q.toEcsDocument(text(input.record))) }));

group("sql-rows", (input) =>
  input.op === "ddl"
    ? { sql: q.createTableSql(input.dialect ?? "sqlite", input.table_name ?? "logs") }
    : { row: parse(q.sqlRow(text(input.record))) },
);

group("graph-rows", (input) =>
  input.op === "statement"
    ? { cypher: q.CYPHER }
    : { row: parse(q.graphRow(text(input.record), input.run_key, input.trace_key)) },
);

group("content-policy", (input) => {
  if (input.error_policy !== undefined) {
    try {
      q.applyPolicyValue("null", input.error_policy);
      return { ok: true };
    } catch (error) {
      return { error: error.message };
    }
  }
  const field = input.field ?? "input_messages";
  const meta = `{${JSON.stringify(field)}:${input.json}}`;
  return { value: parse(q.applyContentPolicy(meta, input.policy))[field] };
});

group("field-classes", () => ({
  registry: Object.fromEntries(q.fieldClasses().map((entry) => [entry.key, entry.class])),
  content_fields: q.contentFields(),
  keys_in_class: {
    secret: q.keysInClass("secret"),
    pii: q.keysInClass("pii"),
    content: q.keysInClass("content"),
  },
}));

// ---- hash chains ---------------------------------------------------------------------------

function mutate(chain, steps) {
  const out = [...chain];
  for (const step of steps ?? []) {
    const at = Number(plain(step.index ?? 0));
    switch (step.type) {
      case "edit_message":
        out[at].message = step.value;
        break;
      case "edit_level":
        out[at].level = step.value;
        break;
      case "edit_schema_version":
        out[at].schema_version = step.value;
        break;
      case "edit_meta":
        out[at].meta[step.key] = step.value;
        break;
      case "edit_llm":
        out[at].llm[step.key] = step.value;
        break;
      case "edit_hash":
        out[at].meta.hash = step.value;
        break;
      case "edit_prev_hash":
        out[at].meta.prev_hash = step.value;
        break;
      case "drop_hash":
        delete out[at].meta.hash;
        break;
      case "delete":
        out.splice(at, 1);
        break;
      case "swap": {
        const other = Number(plain(step.other));
        [out[at], out[other]] = [out[other], out[at]];
        break;
      }
      case "truncate":
        out.length = Number(plain(step.keep));
        break;
      default:
        throw new Error(`unknown mutation ${step.type}`);
    }
  }
  return out;
}

const verdict = (v) => ({
  ok: v.ok,
  records_checked: v.recordsChecked,
  broken_at: v.brokenAt ?? null,
  reason: v.reason ?? null,
  head_hash: v.headHash ?? null,
});

group("hash-chain", (input) => {
  const genesis = input.genesis;
  const chain = new q.Chain(genesis);
  // keep the stamped records as text: the hash covers each number's spelling
  const stamped = input.records.map((record) => parseLossless(chain.stamp(text(record))));
  const got = { chain: stamped, head_hash: chain.head() };
  const key = input.key === undefined ? undefined : Buffer.from(input.key);
  const signature = key === undefined ? undefined : chain.signHead(key);
  if (signature !== undefined) got.signature = signature;
  // mutate a copy (re-parsed, so number spellings survive); `stamped` is reported as it was
  const mutated = text(mutate(parseLossless(text(stamped)), input.mutations));
  got.verify = verdict(q.verifyChain(mutated, genesis));
  if (key !== undefined) got.verify_signed = verdict(q.verifySignedChain(mutated, key, signature, genesis));
  return got;
});

// ---- stateful plugins, stepped through an injected clock and random draw --------------------

/** Runs `steps`, carrying the clock and the random draw forward the way the Rust harness does. */
function scenario(input, onStep) {
  let clock = number(input.start, 0);
  let draw = 0;
  return input.steps.map((step) => {
    if (step.clock !== undefined) clock = number(step.clock);
    if (step.rng !== undefined) draw = number(step.rng);
    return onStep(step, clock, draw);
  });
}

const messages = (released) => released.map((r) => parse(r).message);

group("sampling", (input) => {
  let sampler;
  try {
    sampler = new q.Sampling(camelKeys({ rate: 0, ...plain(input.options) }));
  } catch (error) {
    return { error: error.message };
  }
  const steps = scenario(input, (step, _clock, draw) => {
    const d = sampler.decide(text(step.record), draw);
    return { kept: d.kept, written: messages(d.released) };
  });
  return {
    steps,
    state: {
      buffered_traces: parse(sampler.bufferedTraces()),
      buffered_records: sampler.bufferedRecords(),
      elevated: parse(sampler.elevated()),
    },
  };
});

group("rate-limit", (input) => {
  let limiter;
  try {
    limiter = new q.RateLimit(camelKeys({ per_seconds: 1, ...plain(input.options) }));
  } catch (error) {
    return { error: error.message };
  }
  const steps = scenario(input, (step, clock) => ({
    kept: limiter.allow(text(step.record), clock),
    written: [],
  }));
  return { steps, state: { windows: parse(limiter.windows()) } };
});

group("adaptive-sampling", (input) => {
  let sampler;
  try {
    sampler = new q.AdaptiveSampling(number(input.start, 0), camelKeys(plain(input.options)));
  } catch (error) {
    return { error: error.message };
  }
  const steps = scenario(input, (step, clock, draw) => ({
    kept: sampler.decide(text(step.record), clock, draw),
    written: [],
  }));
  const bytes = sampler.windowBytes();
  return {
    steps,
    state: {
      rate: sampler.rate,
      window_demand_bytes: bytes.demand,
      window_emitted_bytes: bytes.emitted,
    },
  };
});

group("flight-recorder", (input) => {
  const recorder = new q.FlightRecorder(camelKeys(plain(input.options)));
  const steps = input.steps.map((step) => {
    const d = recorder.decide(text(step.record));
    return { kept: d.kept, written: messages(d.released) };
  });
  return {
    steps,
    state: {
      buffered_runs: parse(recorder.bufferedRuns()),
      buffered_records: recorder.bufferedRecords(),
      flushed: parse(recorder.flushed()),
    },
  };
});

group("run-summary", (input) => {
  const summary = new q.RunSummary(camelKeys(plain(input.options)));
  const steps = input.steps.map((step) => {
    const written = summary.observe(text(step.record), "t");
    if (written === null || written === undefined) return { written: [] };
    const record = parse(written);
    delete record.timestamp;
    return { written: [record] };
  });
  return { steps, state: { open_runs: parse(summary.openRuns()) } };
});

group("run-plugin", (input) => {
  const given = input.options?.run_id;
  const generated = given === undefined || given === "";
  const stamp = new q.RunStamp(given, "generated");
  const steps = input.steps.map((step) => {
    const meta = parse(stamp.stamp(text(step.record.meta ?? {})));
    if (generated && meta.run_id === "generated") meta.run_id = "<generated>";
    return { kept: true, meta, written: [] };
  });
  return { steps, state: { step: stamp.steps() } };
});

group("alert-dedupe", (input) => {
  const dedupe = new q.AlertDedupe(camelKeys(plain(input.options)));
  const sent = (list) => list.map(({ message, occurrences }) => ({ message, occurrences }));
  const steps = input.steps.map((step) => {
    const at = number(step.at);
    const out = step.record === undefined ? dedupe.advance(at) : dedupe.observe(text(step.record), at);
    return { sent: sent(out) };
  });
  let after = [];
  if (input.end !== undefined) after = sent(dedupe.advance(number(input.end)));
  else if (input.close === true) dedupe.close();
  return { steps, after, tracked: dedupe.tracked() };
});

// ---- levels, records, ids, GenAI attributes, trace trees -------------------------------------

group("levels", (input) => {
  try {
    return { level: q.levelFromJson(text(input.value)) };
  } catch (error) {
    return { error: error.message };
  }
});

group("records", (input) => {
  switch (input.op) {
    case "timestamp":
      return { timestamp: q.formatTimestamp(number(input.unix_micros)) };
    case "llm_block": {
      const built = q.buildLlmBlock(text(input.fields));
      return { block: built.block === null || built.block === undefined ? null : parse(built.block), warnings: built.warnings };
    }
    default:
      try {
        return { record: parse(q.parseRecord(text(input.record))) };
      } catch (error) {
        return { error: error.message };
      }
  }
});

group("trace-headers", (input) => ({ trace_id: q.parseTraceHeader(input.header) ?? null }));

group("otel-ids", (input) => {
  switch (input.op) {
    case "span_id":
      return { id: q.spanIdFor(input.value) };
    case "trace_id":
      return { id: q.traceIdFor(text(input.meta ?? {})) ?? null };
    default:
      return { id: q.traceIdFromParent(input.value) };
  }
});

group("semconv", (input) => {
  const convention = input.convention ?? "latest";
  switch (input.op) {
    case "resolve":
      try {
        return parse(q.semconvResolve(input.explicit));
      } catch (error) {
        return { error: error.message };
      }
    case "capture":
      return { enabled: q.captureContentEnabled(input.env?.[q.CAPTURE_ENV]) };
    case "epoch": {
      const ns = q.epochNs(input.timestamp);
      return parseLossless(`{"ns":${ns ?? "null"}}`);
    }
    case "classify":
      return q.classifyRecord(text(input.record));
    case "log_attributes":
      return { attributes: parse(q.logAttributes(text(input.record), convention)) };
    default: {
      const spec = q.exportSpec(
        text(input.record),
        convention,
        input.default_provider ?? "unknown",
        input.capture_content ?? false,
      );
      return { span: spec === null || spec === undefined ? null : parse(spec) };
    }
  }
});

group("trace-tree", (input) => parse(q.buildTrace(text(input.records), input.run_id)));
