// The WASM build in a Cloudflare Worker. A Worker cannot fetch or compile WebAssembly at run time, so the .wasm is
// imported as a precompiled module (wrangler turns the import into a `WebAssembly.Module`) and handed to initSync and
// to the SDK's initWasm. A request to any path runs the shared harness and the SDK checks and answers with JSON.
import wasm from "../../pkg/logquill_wasm_bg.wasm";

import * as sdk from "../../dist/browser.mjs";
import * as q from "../../js/index.mjs";
import { merge, report, runAll } from "./harness.mjs";
import { runSdk } from "./sdk-harness.mjs";

q.initSync(wasm);

export default {
  async fetch() {
    const result = merge(runAll(q), await runSdk(sdk, wasm));
    return Response.json({ ...result, host: `Cloudflare Workers (${navigator.userAgent})`, report: report({ ...result, host: "Cloudflare Workers" }) });
  },
};
