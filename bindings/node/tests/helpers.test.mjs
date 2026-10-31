// The pure helpers, checked against the project's shared fixtures: the same golden files and
// corpus groups the Rust core and the Python binding run.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

// The module under test: the native addon, or another binding's build of the same API (`bindings/wasm` sets this).
const logquill = await import(process.env.LOGQUILL_TARGET ?? "../index.js");
import { LOSSLESS, plain, readLossless, SCHEMA, toText } from "./lossless.mjs";

const golden = readLossless(`${SCHEMA}golden_records.json`);
const chain = readLossless(`${SCHEMA}golden_chain.json`);
const corpus = (group) => readLossless(`${SCHEMA}corpus/${group}.json`).cases;
const skip = LOSSLESS ? false : "needs Node 21+ (JSON.parse source text access)";

describe("golden records", { skip }, () => {
  for (const { name, record } of golden.valid) {
    it(`accepts ${name} unchanged`, () => {
      assert.deepEqual(JSON.parse(logquill.parseRecord(toText(record))), plain(record));
    });
  }
  for (const { name, record, parsed } of golden.legacy) {
    it(`reads the legacy record ${name} in its current form`, () => {
      assert.deepEqual(JSON.parse(logquill.parseRecord(toText(record))), plain(parsed));
    });
  }
  for (const { name, record } of golden.rejected) {
    it(`rejects ${name} with a reason`, () => {
      assert.throws(() => logquill.parseRecord(toText(record)), (error) => error.message.length > 0);
    });
  }
});

describe("hash chain", { skip }, () => {
  it("verifies the shared chain and reports its head", () => {
    const result = logquill.verifyChain(toText(chain));
    assert.equal(result.ok, true);
    assert.equal(result.recordsChecked, chain.length);
    assert.equal(result.headHash, chain.at(-1).meta.hash);
  });

  it("catches an edit, a removal and a reorder", () => {
    const edited = structuredClone(chain);
    edited[1].message = "changed";
    assert.equal(logquill.verifyChain(toText(edited)).ok, false);

    const removed = logquill.verifyChain(toText([chain[0], ...chain.slice(2)]));
    assert.equal(removed.ok, false);
    assert.equal(removed.brokenAt, 2);

    assert.equal(logquill.verifyChain(toText([chain[1], chain[0], ...chain.slice(2)])).ok, false);
  });

  it("takes a custom genesis and rejects input that is not an array", () => {
    assert.equal(logquill.verifyChain(toText(chain), "f".repeat(64)).ok, false);
    assert.throws(() => logquill.verifyChain("{}"), /JSON array/);
    assert.throws(() => logquill.verifyChain("not json"), /not valid JSON/);
  });
});

describe("shared corpus", { skip }, () => {
  for (const { name, input, expected } of corpus("canonical-json")) {
    it(`canonical-json: ${name}`, () => {
      assert.equal(logquill.canonicalJson(input.json), expected.text);
    });
  }

  const formatters = { json: logquill.formatJson, text: logquill.formatText, logfmt: logquill.formatLogfmt };
  for (const { name, input, expected } of corpus("formatters")) {
    it(`formatters: ${name}`, () => {
      if (input.op === "parse_logfmt") {
        assert.deepEqual(JSON.parse(logquill.parseLogfmt(input.line)), plain(expected.fields));
      } else {
        // a few cases carry the record as text, because it holds NaN or Infinity words
        assert.equal(formatters[input.op](input.record_json ?? toText(input.record)), expected.line);
      }
    });
  }
});

describe("small things", () => {
  it("stamps a record timestamp in the contract's form", () => {
    assert.match(logquill.utcTimestamp(), /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/);
  });

  it("exposes the genesis hash", () => {
    assert.match(logquill.GENESIS_HASH, /^[0-9a-f]{64}$/);
  });
});
