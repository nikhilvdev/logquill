// The WASM module as the Node corpus tests (bindings/node/tests) load it: LOGQUILL_TARGET points here.
// Loads the .wasm from disk and re-exports the public API, so the same test files that check the native
// addon check this build of the core.
import { readFileSync } from "node:fs";

import { initSync } from "../js/index.mjs";

initSync(readFileSync(new URL("../pkg/logquill_wasm_bg.wasm", import.meta.url)));

export * from "../js/index.mjs";
