import { copyFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { defineConfig } from "tsup";

const nativeWasm = fileURLToPath(new URL("./sdk/native-wasm.ts", import.meta.url));
const nodeSdk = fileURLToPath(new URL("../node/sdk/src/", import.meta.url));

// The browser SDK is the Node SDK's own source, built for the browser with three things swapped for browser versions:
// `#span` and `#context` (a plain stack instead of AsyncLocalStorage, through the package's `imports` map and the
// "browser" condition) and the native-addon loader, which becomes the WASM core.
const wasmCore = {
  name: "wasm-core",
  setup(build: { onResolve: (o: { filter: RegExp }, cb: (a: { path: string; importer: string }) => { path: string } | undefined) => void }) {
    build.onResolve({ filter: /^#native$/ }, () => ({ path: nativeWasm }));
    // plugins that import the loader directly (`../core/native.js`) get the same swap
    build.onResolve({ filter: /(^|\/)native\.js$/ }, (args) => (args.importer.startsWith(nodeSdk) ? { path: nativeWasm } : undefined));
  },
};

export default defineConfig({
  entry: { browser: "sdk/browser.ts" },
  format: ["esm"],
  platform: "browser",
  dts: true,
  sourcemap: true,
  clean: true,
  splitting: false,
  treeshake: true,
  outExtension() {
    return { js: ".mjs" };
  },
  esbuildPlugins: [wasmCore],
  esbuildOptions(options) {
    options.conditions = [...(options.conditions ?? []), "browser"];
  },
  // `initWasm()` with no argument fetches the module from beside the bundle
  onSuccess: async () => {
    copyFileSync(fileURLToPath(new URL("./pkg/logquill_wasm_bg.wasm", import.meta.url)), fileURLToPath(new URL("./dist/logquill_wasm_bg.wasm", import.meta.url)));
  },
});
