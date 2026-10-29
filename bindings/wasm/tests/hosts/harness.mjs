// The shared corpus and golden checks, written for any JavaScript host: no Node built-ins, no filesystem, no test
// framework. A browser page, Deno and a Cloudflare Worker each load the WASM build, hand it to `runAll`, and report
// what comes back. The group definitions are the Node binding's own (bindings/node/tests/corpus-groups.mjs), and the
// golden / canonical-json / formatters checks mirror bindings/node/tests/helpers.test.mjs, so a host passes exactly
// when the native addon does.
import { corpusGroups } from "../../../node/tests/corpus-groups.mjs";
import { LOSSLESS, parseLossless, plain, toText } from "../../../node/tests/lossless-core.mjs";
import { files } from "./fixtures.generated.mjs";

/** Deep equality for JSON values (key order does not matter, number types do not blur). */
function same(a, b) {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  return ka.length === kb.length && ka.every((k) => Object.hasOwn(b, k) && same(a[k], b[k]));
}

function expectSame(got, want) {
  if (!same(got, want)) throw new Error(`expected ${JSON.stringify(want)}\n     got ${JSON.stringify(got)}`);
}

function expectThrows(fn, pattern) {
  try {
    fn();
  } catch (error) {
    if (pattern && !pattern.test(String(error?.message))) throw new Error(`threw "${error?.message}", wanted ${pattern}`);
    return;
  }
  throw new Error("expected an error, got none");
}

const fixture = (name) => parseLossless(files[name]);
const cases = (group) => fixture(`corpus/${group}.json`).cases;

/**
 * Runs everything against `q` (a binding's public API, in the Node addon's shape). Returns `{host, total, passed, failures}`.
 * `skip` names corpus groups the binding does not expose yet (the C ABI has no stateful-plugin classes); skipped groups are
 * not counted, so a binding cannot hide a gap by skipping silently: the caller has to list each one.
 */
export function runAll(q, { skip = [] } = {}) {
  const failures = [];
  let total = 0;
  const check = (name, fn) => {
    total += 1;
    try {
      fn();
    } catch (error) {
      failures.push({ name, error: String(error?.message ?? error) });
    }
  };
  const host =
    typeof Deno !== "undefined"
      ? `Deno ${Deno.version.deno}`
      : typeof navigator !== "undefined"
        ? navigator.userAgent
        : "unknown";

  // Without source-text access a `1.0` cannot be told from `1`, and the corpus cannot be read faithfully. A
  // silent skip would hide that, so it is a failure here (the Node tests skip, with a reason).
  if (!LOSSLESS) {
    return { host, total: 1, passed: 0, failures: [{ name: "JSON.parse source text access", error: "this host's JSON.parse does not pass `context.source` to a reviver; the corpus cannot be read losslessly" }] };
  }

  for (const { name, run } of corpusGroups(q)) {
    if (skip.includes(name)) continue;
    for (const { name: label, input, expected } of cases(name)) {
      check(`corpus: ${name}: ${label}`, () => expectSame(plain(run(input)), plain(expected)));
    }
  }

  const golden = fixture("golden_records.json");
  for (const { name, record } of golden.valid) {
    check(`golden: accepts ${name} unchanged`, () => expectSame(JSON.parse(q.parseRecord(toText(record))), plain(record)));
  }
  for (const { name, record, parsed } of golden.legacy) {
    check(`golden: reads the legacy record ${name}`, () => expectSame(JSON.parse(q.parseRecord(toText(record))), plain(parsed)));
  }
  for (const { name, record } of golden.rejected) {
    check(`golden: rejects ${name} with a reason`, () => expectThrows(() => q.parseRecord(toText(record)), /./));
  }

  const chain = fixture("golden_chain.json");
  check("hash chain: verifies the shared chain and reports its head", () => {
    const result = q.verifyChain(toText(chain));
    expectSame([result.ok, result.recordsChecked, result.headHash], [true, chain.length, chain.at(-1).meta.hash]);
  });
  check("hash chain: catches an edit, a removal and a reorder", () => {
    const edited = structuredClone(chain);
    edited[1].message = "changed";
    expectSame(q.verifyChain(toText(edited)).ok, false);
    const removed = q.verifyChain(toText([chain[0], ...chain.slice(2)]));
    expectSame([removed.ok, removed.brokenAt], [false, 2]);
    expectSame(q.verifyChain(toText([chain[1], chain[0], ...chain.slice(2)])).ok, false);
  });
  check("hash chain: takes a custom genesis and rejects input that is not an array", () => {
    expectSame(q.verifyChain(toText(chain), "f".repeat(64)).ok, false);
    expectThrows(() => q.verifyChain("{}"), /JSON array/);
    expectThrows(() => q.verifyChain("not json"), /not valid JSON/);
  });

  for (const { name, input, expected } of cases("canonical-json")) {
    check(`canonical-json: ${name}`, () => expectSame(q.canonicalJson(input.json), expected.text));
  }
  const formatters = { json: q.formatJson, text: q.formatText, logfmt: q.formatLogfmt };
  for (const { name, input, expected } of cases("formatters")) {
    check(`formatters: ${name}`, () => {
      if (input.op === "parse_logfmt") expectSame(JSON.parse(q.parseLogfmt(input.line)), plain(expected.fields));
      else expectSame(formatters[input.op](input.record_json ?? toText(input.record)), expected.line);
    });
  }

  check("small things: stamps a record timestamp in the contract's form", () => {
    if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(q.utcTimestamp())) throw new Error(q.utcTimestamp());
  });
  check("small things: exposes the genesis hash", () => {
    if (!/^[0-9a-f]{64}$/.test(q.GENESIS_HASH)) throw new Error(String(q.GENESIS_HASH));
  });

  return { host, total, passed: total - failures.length, failures };
}

/** The results of several runs on one host, as one. */
export function merge(...runs) {
  return {
    host: runs[0].host,
    total: runs.reduce((n, r) => n + r.total, 0),
    passed: runs.reduce((n, r) => n + r.passed, 0),
    failures: runs.flatMap((r) => r.failures),
  };
}

/** One line per failure, then a summary. */
export function report({ host, total, passed, failures }) {
  const lines = failures.slice(0, 40).map((f) => `FAIL ${f.name}\n  ${f.error}`);
  if (failures.length > 40) lines.push(`... and ${failures.length - 40} more`);
  lines.push(`${host}: ${passed}/${total} passed`);
  return lines.join("\n");
}
