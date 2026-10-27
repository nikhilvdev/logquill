// Copies the files `napi build --target wasm32-wasip1-threads` produced (into ./wasm-out) next to the
// native loader, where `index.js` looks for them when no native binary exists for the platform.
// It does not copy the WebAssembly build's own `index.js` / `index.d.ts`: those describe the core-only build,
// and the package's types are the full build's.
import { copyFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const from = join(root, "wasm-out");
const files = [
  "logquill.wasm32-wasi.wasm",
  "logquill.wasi.cjs",
  "logquill.wasi-browser.js",
  "wasi-worker.mjs",
  "wasi-worker-browser.mjs",
];
for (const file of files) {
  if (!existsSync(join(from, file))) {
    console.error(`place-wasm: ${file} is missing from wasm-out — run \`npm run build:wasm\``);
    process.exit(1);
  }
  copyFileSync(join(from, file), join(root, file));
}
console.log(`place-wasm: ${String(files.length)} files placed next to index.js`);
