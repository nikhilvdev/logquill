// Runs the Node binding's corpus tests (bindings/node/tests) against the WASM build, in Node: the same test
// files that check the native addon, with LOGQUILL_TARGET pointing at this build. Every group must pass.
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

const node = fileURLToPath(new URL("../../node/", import.meta.url));
const target = pathToFileURL(fileURLToPath(new URL("../tests/node-target.mjs", import.meta.url))).href;

const done = spawnSync("node", ["--test", "tests/*.test.mjs"], {
  cwd: node,
  stdio: "inherit",
  env: { ...process.env, LOGQUILL_TARGET: target },
});
process.exit(done.status ?? 1);
