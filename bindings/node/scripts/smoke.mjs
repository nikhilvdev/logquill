// Consumer smoke test: load the built SDK the way a user would, once as ESM and once as CommonJS, log through
// the Rust file transport and check what reached the disk. Run `npm run build && npm run build:sdk` first.
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
for (const file of ["dist/index.mjs", "dist/index.cjs", "index.js"]) {
  if (!existsSync(join(root, file))) {
    console.error(`smoke: ${file} is missing — run \`npm run build && npm run build:sdk\` first`);
    process.exit(1);
  }
}

const dir = mkdtempSync(join(tmpdir(), "logquill-smoke-"));
const body = (log) => `
const log = new Logger("smoke", { transports: [new FileTransport(${JSON.stringify(log)})], meta: { service: "smoke" } });
if (!new FileTransport(${JSON.stringify(log + ".probe")}).nativeHandle) throw new Error("the Rust file transport is not in use");
for (let i = 0; i < 500; i++) log.info("hello", { i });
// no close(): the exit hook has to drain the Rust queue
`;
const esm = join(dir, "consumer.mjs");
writeFileSync(
  esm,
  `import { Logger, FileTransport } from ${JSON.stringify(join(root, "dist/index.mjs"))};${body(join(dir, "esm.log"))}`,
);
const cjs = join(dir, "consumer.cjs");
writeFileSync(
  cjs,
  `const { Logger, FileTransport } = require(${JSON.stringify(join(root, "dist/index.cjs"))});${body(join(dir, "cjs.log"))}`,
);

for (const [kind, script, log] of [
  ["ESM", esm, "esm.log"],
  ["CJS", cjs, "cjs.log"],
]) {
  execFileSync(process.execPath, [script], { stdio: "inherit" });
  const lines = readFileSync(join(dir, log), "utf8").trim().split("\n");
  if (lines.length !== 500) throw new Error(`${kind}: expected 500 lines, found ${String(lines.length)}`);
  const last = JSON.parse(lines.at(-1));
  if (last.schema_version !== "2.0" || last.meta.service !== "smoke" || last.meta.i !== 499) {
    throw new Error(`${kind}: the last record is wrong: ${lines.at(-1)}`);
  }
  console.log(`smoke ok: ${kind} consumer wrote 500 records through the Rust file transport`);
}
