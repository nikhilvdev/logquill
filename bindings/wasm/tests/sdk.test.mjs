// The browser SDK checks (tests/hosts/sdk-harness.mjs) run in Node, as one test per check so a failure names itself.
// The other hosts (Chrome, Deno, Bun, Workers) run the same checks in tests/hosts. Needs `npm run build:sdk` first.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

import * as sdk from "../dist/browser.mjs";
import { runSdk } from "./hosts/sdk-harness.mjs";

describe("browser SDK on the WASM core", () => {
  it("passes every check", async () => {
    const result = await runSdk(sdk, readFileSync(new URL("../dist/logquill_wasm_bg.wasm", import.meta.url)));
    const detail = result.failures.map((f) => `${f.name}: ${f.error}`).join("\n");
    assert.equal(result.failures.length, 0, detail);
    assert.ok(result.total >= 16, `only ${result.total} checks ran`);
  });
});
