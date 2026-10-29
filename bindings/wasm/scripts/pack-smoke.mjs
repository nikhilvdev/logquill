// The gate before publishing: packs the package as npm would, checks what is inside, installs the tarball into an
// empty project and uses it by name, the way a consumer does. Needs `npm run build` and `npm run build:sdk` first.
// Nothing is published: `npm pack` only writes a tarball into a scratch directory.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const scratch = mkdtempSync(join(tmpdir(), "lq-wasm-pack-"));
const run = (cmd, args, cwd) => execFileSync(cmd, args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] });
const fail = (message) => {
  console.error(`pack-smoke FAILED: ${message}`);
  process.exit(1);
};

// ---- what goes into the tarball --------------------------------------------------------------------------------
const [packed] = JSON.parse(run("npm", ["pack", "--json", "--pack-destination", scratch], root));
const files = packed.files.map((f) => f.path);
const manifest = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));

const allowed = [/^package\.json$/, /^README\.md$/, /^dist\//, /^js\/index\.mjs$/, /^pkg\/logquill_wasm(_bg)?\.(js|wasm)$/];
const stray = files.filter((f) => !allowed.some((pattern) => pattern.test(f)));
if (stray.length > 0) fail(`files that should not ship: ${stray.join(", ")}`);

const targets = (value) => (typeof value === "string" ? [value] : Object.values(value).flatMap(targets));
for (const target of targets(manifest.exports)) {
  if (!files.includes(target.replace(/^\.\//, ""))) fail(`exports points at ${target}, which is not in the tarball`);
}
if (/(^|[/_.-])(secret|credential|\.env|\.pem|\.key)/i.test(files.join("\n"))) fail("a secret-looking file is in the tarball");

// ---- install it into a clean project and use it by name ----------------------------------------------------------
writeFileSync(join(scratch, "package.json"), JSON.stringify({ name: "consumer", version: "1.0.0", type: "module", private: true }));
run("npm", ["install", "--ignore-scripts", "--no-audit", "--no-fund", join(scratch, packed.filename)], scratch);
writeFileSync(
  join(scratch, "use.mjs"),
  `
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { initWasm, Logger, CollectingTransport, RedactPlugin } from "@logquill/wasm";
import { initSync, canonicalJson } from "@logquill/wasm/core";

const wasm = fileURLToPath(import.meta.resolve("@logquill/wasm/logquill_wasm_bg.wasm"));
const bytes = readFileSync(wasm);
await initWasm(bytes);
initSync(bytes); // the raw core is its own module instance, so it is loaded separately
const sink = new CollectingTransport();
const log = new Logger("consumer", { transports: [sink], plugins: [new RedactPlugin()] });
log.info("hello", { password: "p", keep: 1 });
await log.flush();
console.log(JSON.stringify([sink.records[0].meta, canonicalJson('{"b":1,"a":2}')]));
`,
);
const out = run("node", ["use.mjs"], scratch).trim();
// canonical JSON is Python's separators (", " and ": "), which is what the tamper chain hashes
if (out !== '[{"password":"***","keep":1},"{\\"a\\": 2, \\"b\\": 1}"]') fail(`the installed package logged ${out}`);
console.log(`pack-smoke ok: ${packed.filename}, ${files.length} files, ${(packed.size / 1024).toFixed(0)} KiB packed; installed into a clean project and used by name`);
