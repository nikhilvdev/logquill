// What only the WASM build has to get right, beyond the shared corpus: loading, the JavaScript clock, and the
// error shape across the boundary. Run with `node --test tests/*.test.mjs` from bindings/wasm after `npm run build`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

import * as q from "../js/index.mjs";

const bytes = readFileSync(new URL("../pkg/logquill_wasm_bg.wasm", import.meta.url));

describe("loading", () => {
  it("initSync loads from bytes and publishes the constants", () => {
    q.initSync(bytes);
    assert.match(q.GENESIS_HASH, /^[0-9a-f]{64}$/);
    assert.ok(q.CYPHER.length > 0);
    assert.ok(q.LEAF_PLACEHOLDER.length > 0);
    assert.ok(q.CAPTURE_ENV.length > 0);
  });

  it("init loads from a compiled module, the way a Worker passes one", async () => {
    await q.init(await WebAssembly.compile(bytes));
    assert.equal(q.formatTimestamp(0), "1970-01-01T00:00:00.000Z");
  });
});

describe("the clock is Date.now(), not SystemTime (which panics on this target)", () => {
  it("utcTimestamp is within a second of Date.now()", () => {
    q.initSync(bytes);
    const stamp = q.utcTimestamp();
    assert.match(stamp, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/);
    assert.ok(Math.abs(Date.parse(stamp) - Date.now()) < 1000);
  });
});

describe("errors cross the boundary as ordinary Errors with the core's message", () => {
  it("bad JSON names what was wrong and what to do", () => {
    q.initSync(bytes);
    assert.throws(() => q.formatJson("not json"), (e) => e instanceof Error && /not valid JSON.*JSON\.stringify/.test(e.message));
  });

  it("an unknown policy is refused with a message", () => {
    q.initSync(bytes);
    assert.throws(() => q.applyPolicyValue("null", "loud"), (e) => e instanceof Error && e.message.length > 0);
  });
});

describe("keys are bytes", () => {
  it("signHead takes a Uint8Array, and the signature verifies", () => {
    q.initSync(bytes);
    const key = new TextEncoder().encode("secret-key");
    const signature = q.signHead(q.GENESIS_HASH, key);
    assert.equal(q.verifyHeadSignature(q.GENESIS_HASH, key, signature), true);
    assert.equal(q.verifyHeadSignature(q.GENESIS_HASH, new TextEncoder().encode("other"), signature), false);
  });
});
