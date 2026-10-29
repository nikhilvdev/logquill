// The browser build must not reach for Node: a page, Deno and a Worker have no `node:` modules, `require`, `process` or
// `Buffer` (a `typeof Buffer` guard is fine). This reads the sources that ship to those hosts, the same rule the
// Node SDK's browser entry is held to (bindings/node/sdk/test/browser/no-node-builtins.test.ts). The host runs in
// tests/hosts prove it by actually loading the module; this catches a regression before a host is even started.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

const shipped = [
  "../js/index.mjs",
  "../pkg/logquill_wasm.js",
  "../dist/browser.mjs",
  "../sdk/native-wasm.ts",
  "../sdk/browser.ts",
  "./hosts/sdk-harness.mjs",
  "./hosts/harness.mjs",
  "./hosts/chrome.html",
  "./hosts/worker.mjs",
  "../../node/tests/corpus-groups.mjs",
  "../../node/tests/lossless-core.mjs",
];

const forbidden = [
  [/\bfrom\s+["']node:/, "an import from a node: module"],
  [/\bimport\(\s*["']node:/, "a dynamic import of a node: module"],
  [/\brequire\s*\(/, "a require() call"],
  [/\b__dirname\b|\b__filename\b/, "a CommonJS path global"],
];

describe("no Node built-ins in what ships to a browser, Deno or a Worker", () => {
  for (const path of shipped) {
    it(path, () => {
      const source = readFileSync(new URL(path, import.meta.url), "utf8")
        .split("\n")
        .filter((line) => !/^\s*(\/\/|\*|\/\*)/.test(line)) // comments may name them
        .join("\n");
      for (const [pattern, what] of forbidden) {
        assert.ok(!pattern.test(source), `${path} contains ${what}`);
      }
      // `process` and Buffer are Node's; using them is fine only where the file first checks that they exist.
      if (/\bprocess\.(env|argv|versions|platform|cwd|exit)/.test(source)) assert.match(source, /typeof process\s*===\s*["']undefined["']/, `${path} uses process without a typeof guard`);
      // Buffer is Node's; using it is fine only where the file first checks that it exists.
      if (/\bBuffer\b/.test(source)) assert.match(source, /typeof Buffer\s*===\s*["']undefined["']/, `${path} uses Buffer without a typeof guard`);
    });
  }
});
