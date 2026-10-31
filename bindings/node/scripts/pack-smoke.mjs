// Packaging smoke test: pack the package and a platform package for this machine, install both into a clean
// project, and import `logquill` by name, once as ESM and once as CommonJS, the way a user would.
// It checks what `scripts/smoke.mjs` cannot: the `exports` map, the `files` allowlist, the generated native
// loader finding `@logquill/native-<platform>`, and that no source or test file is shipped.
// Run `npm run build && npm run build:sdk` first.
import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const manifest = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
const binary = readdirSync(root).find((file) => /^logquill\..+\.node$/.test(file));
if (!binary || !existsSync(join(root, "dist/index.mjs"))) {
  console.error("pack-smoke: build first — `npm run build && npm run build:sdk`");
  process.exit(1);
}
const platform = binary.slice("logquill.".length, -".node".length);
const run = (command, args, cwd) =>
  execFileSync(command, args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] });

const work = mkdtempSync(join(tmpdir(), "logquill-pack-"));

// the main package, packed from the repository exactly as `npm publish` would
const mainTarball = join(work, run("npm", ["pack", "--pack-destination", work, "--silent"], root).trim());
const listing = run("tar", ["-tzf", mainTarball], work).split("\n").filter(Boolean);
const stray = listing.filter(
  (path) =>
    /\/(sdk|src|tests|target|node_modules)\//.test(path) || (/\.(rs|ts)$/.test(path) && !/\.d\.[cm]?ts$/.test(path)),
);
if (stray.length > 0) throw new Error(`the package ships files it should not: ${stray.slice(0, 5).join(", ")}`);
for (const required of ["package/dist/index.mjs", "package/dist/index.cjs", "package/index.js", "package/package.json"]) {
  if (!listing.includes(required)) throw new Error(`the package is missing ${required}`);
}

// the platform package: what `napi prepublish` builds for each target, made by hand for this one
const nativeDir = join(work, "native");
mkdirSync(nativeDir);
copyFileSync(join(root, binary), join(nativeDir, binary));
writeFileSync(
  join(nativeDir, "package.json"),
  JSON.stringify({ name: `@logquill/native-${platform}`, version: manifest.version, main: binary, files: [binary] }),
);
const nativeTarball = join(work, run("npm", ["pack", "--pack-destination", work, "--silent"], nativeDir).trim());

// a clean consumer project
const app = join(work, "app");
mkdirSync(app);
writeFileSync(join(app, "package.json"), JSON.stringify({ name: "consumer", version: "1.0.0", private: true }));
run("npm", ["install", "--no-audit", "--no-fund", "--ignore-scripts", mainTarball, nativeTarball], app);

const body = (file) => `
const log = new Logger("pack", { transports: [new FileTransport(${JSON.stringify(join(app, file))})], meta: { via: "pack" } });
if (!new FileTransport(${JSON.stringify(join(app, file + ".probe"))}).nativeHandle) throw new Error("the Rust file transport is not in use");
for (let i = 0; i < 300; i++) log.info("hello", { i });
`;
writeFileSync(join(app, "c.mjs"), `import { Logger, FileTransport } from "logquill";${body("esm.log")}`);
writeFileSync(join(app, "c.cjs"), `const { Logger, FileTransport } = require("logquill");${body("cjs.log")}`);
for (const [kind, script, log] of [
  ["ESM", "c.mjs", "esm.log"],
  ["CJS", "c.cjs", "cjs.log"],
]) {
  run(process.execPath, [script], app);
  const lines = readFileSync(join(app, log), "utf8").trim().split("\n");
  const last = JSON.parse(lines.at(-1));
  if (lines.length !== 300 || last.meta.via !== "pack" || last.meta.i !== 299) {
    throw new Error(`${kind}: expected 300 records ending at i=299, got ${String(lines.length)} (${lines.at(-1)})`);
  }
  console.log(
    `pack-smoke ok: ${kind} \`import "logquill"\` from an installed tarball wrote 300 records through the Rust file transport`,
  );
}

// The fallback: install only the main package, with no platform package, as on a platform nobody built a
// native binary for. The loader must switch to the WebAssembly build, and the core logic must still work.
if (existsSync(join(root, "logquill.wasm32-wasi.wasm"))) {
  const wasmApp = join(work, "app-wasm");
  mkdirSync(wasmApp);
  writeFileSync(join(wasmApp, "package.json"), JSON.stringify({ name: "consumer-wasm", version: "1.0.0", private: true }));
  run("npm", ["install", "--no-audit", "--no-fund", "--ignore-scripts", mainTarball], wasmApp);
  const check = `
const { Logger, FileTransport, PIIRedactPlugin, CollectingTransport } = require("logquill");
const target = require(${JSON.stringify(join(wasmApp, "node_modules/logquill/index.js"))}).__napiBindingTarget;
if (target !== "wasm32-wasi") throw new Error("expected the WebAssembly build, got " + target);
const sink = new CollectingTransport();
const log = new Logger("wasm", { transports: [sink], plugins: [new PIIRedactPlugin()] });
log.info("signup", { note: "mail jo@example.com or key sk-" + "a".repeat(30) });
log.flush().then(() => {
  const meta = sink.records[0].meta;
  if (meta.note !== "mail *** or key ***") throw new Error("redaction through the WebAssembly core failed: " + meta.note);
  if (new FileTransport(${JSON.stringify(join(wasmApp, "x.log"))}).nativeHandle) throw new Error("no native file transport exists in the WebAssembly build");
  console.log("pack-smoke ok: with no native package, the loader fell back to WebAssembly and the core logic works");
});
`;
  writeFileSync(join(wasmApp, "c.cjs"), check);
  const out = execFileSync(process.execPath, ["--no-warnings", "c.cjs"], { cwd: wasmApp, encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] });
  process.stdout.write(out);
} else {
  console.log("pack-smoke: no WebAssembly build present, skipped the fallback check (run `npm run build:wasm`)");
}
