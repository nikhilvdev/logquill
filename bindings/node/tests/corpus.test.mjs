// The shared corpus (schema/corpus/*.json), group by group, against the native addon. The mapping
// from each case's `input` to a call and back to `expected` mirrors the Rust reference harness
// (crates/logquill-core/tests/{processing,integrity}.rs).
import assert from "node:assert/strict";
import { describe, it } from "node:test";

// The module under test: the native addon, or another binding's build of the same API (`bindings/wasm` sets this).
const q = await import(process.env.LOGQUILL_TARGET ?? "../index.js");
import { corpusGroups } from "./corpus-groups.mjs";
import { LOSSLESS, plain, readLossless, SCHEMA } from "./lossless.mjs";

const skip = LOSSLESS ? false : "needs Node 21+ (JSON.parse source text access)";
const cases = (group) => readLossless(`${SCHEMA}corpus/${group}.json`).cases;

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

for (const { name, run } of corpusGroups(q)) group(name, run);
