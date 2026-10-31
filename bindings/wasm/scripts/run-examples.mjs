// Runs every example in examples/ in the host it is written for, checks what it prints, and checks that README.md
// contains each example verbatim, so a README snippet is always code that was run. The examples import
// "@logquill/wasm"; here that name is pointed at dist/ (build it first: `npm run build && npm run build:sdk`).
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { extname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import puppeteer from "puppeteer-core";
import { unstable_dev } from "wrangler";

const root = fileURLToPath(new URL("../", import.meta.url));
const dist = join(root, "dist");
const bin = (name) => join(root, "node_modules", ".bin", name);
const example = (name) => readFileSync(join(root, "examples", name), "utf8");
const scratch = mkdtempSync(join(tmpdir(), "lq-examples-"));

const failures = [];
const check = (name, ok, detail = "") => {
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${ok ? "" : `\n     ${detail}`}`);
  if (!ok) failures.push(name);
};

/** Points the examples' package name at the built files. `style` is "url" (Deno, Bun) or "path" (a bundler). */
const point = (code, style) => {
  const to = (file) => JSON.stringify(style === "url" ? pathToFileURL(file).href : file);
  return code
    .replaceAll('"@logquill/wasm/logquill_wasm_bg.wasm"', to(join(dist, "logquill_wasm_bg.wasm")))
    .replaceAll('"@logquill/wasm"', to(join(dist, "browser.mjs")));
};

/** The redaction every example should show: the secret and the address are gone, the rest is not. */
const redacted = (text) => /"user":"\*\*\*"/.test(text) && /"password":"\*\*\*"/.test(text) && !/alice@example\.com|hunter2/.test(text);

// ---- the README holds exactly what ran -------------------------------------------------------------------------
const readme = readFileSync(join(root, "README.md"), "utf8");
for (const name of ["browser.html", "deno.mjs", "worker.mjs"]) {
  check(`README contains examples/${name} verbatim`, readme.includes(example(name).trimEnd()), "copy the file's content into the README");
}

// ---- Deno and Bun ----------------------------------------------------------------------------------------------
const server = join(scratch, "server.mjs");
writeFileSync(server, point(example("deno.mjs"), "url"));
const out = (cmd, args) => execFileSync(cmd, args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
check("examples/deno.mjs prints a redacted record in Deno", redacted(out(bin("deno"), ["run", "--allow-read", server])), "see the output of `deno run --allow-read`");
check("examples/deno.mjs prints a redacted record in Bun", redacted(out(bin("bun"), ["run", server])), "see the output of `bun run`");

// ---- headless Chrome -------------------------------------------------------------------------------------------
const TYPES = { ".html": "text/html", ".mjs": "text/javascript", ".wasm": "application/wasm" };
const http = createServer(async (request, response) => {
  const path = new URL(request.url, "http://x").pathname;
  if (path === "/example.html") {
    return void response.writeHead(200, { "content-type": "text/html" }).end(example("browser.html").replaceAll('"@logquill/wasm"', '"/dist/browser.mjs"'));
  }
  try {
    const body = await readFile(join(dist, path.replace(/^\/dist\//, "")));
    response.writeHead(200, { "content-type": TYPES[extname(path)] ?? "application/octet-stream" }).end(body);
  } catch {
    response.writeHead(404).end();
  }
});
await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
const browser = await puppeteer.launch({
  headless: true,
  ...(process.env.CI ? { args: ["--no-sandbox"] } : {}),
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : { channel: "chrome" }),
});
try {
  const page = await browser.newPage();
  const lines = [];
  page.on("console", (message) => lines.push(message.text()));
  page.on("pageerror", (error) => lines.push(`page error: ${error.message}`));
  await page.goto(`http://127.0.0.1:${http.address().port}/example.html`);
  await page.waitForFunction(() => true);
  for (let i = 0; i < 100 && !lines.some((l) => l.includes("signed in")); i++) await new Promise((r) => setTimeout(r, 100));
  check("examples/browser.html logs a redacted record in Chrome", redacted(lines.join("\n")), lines.join(" | "));
} finally {
  await browser.close();
  http.close();
}

// ---- a Worker, locally under workerd ---------------------------------------------------------------------------
const worker = join(scratch, "worker.mjs");
writeFileSync(worker, point(example("worker.mjs"), "path"));
writeFileSync(join(scratch, "wrangler.toml"), 'name = "example"\nmain = "worker.mjs"\ncompatibility_date = "2026-10-01"\n');
const dev = await unstable_dev(worker, { config: join(scratch, "wrangler.toml"), local: true, experimental: { disableExperimentalWarning: true } });
try {
  const meta = await (await dev.fetch("/")).json();
  check("examples/worker.mjs answers with the redacted meta in a Worker", meta.token === "***" && meta.path === "/", JSON.stringify(meta));
} finally {
  await dev.stop();
}

process.exit(failures.length === 0 ? 0 : 1);
