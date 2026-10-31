// Runs the shared harness in headless Chrome. Serves bindings/ over HTTP (a page cannot import from file://),
// opens tests/hosts/chrome.html, waits for window.__result, prints it, and exits non-zero on any failure.
// CHROME_PATH picks a specific binary; otherwise puppeteer finds the installed Chrome stable.
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

import puppeteer from "puppeteer-core";

const root = fileURLToPath(new URL("../../", import.meta.url)); // bindings/
const TYPES = { ".html": "text/html", ".mjs": "text/javascript", ".js": "text/javascript", ".wasm": "application/wasm" };

const server = createServer(async (request, response) => {
  const path = normalize(join(root, decodeURIComponent(new URL(request.url, "http://x").pathname)));
  if (!path.startsWith(root)) return void response.writeHead(403).end();
  try {
    const body = await readFile(path);
    response.writeHead(200, { "content-type": TYPES[extname(path)] ?? "application/octet-stream" }).end(body);
  } catch {
    response.writeHead(404).end();
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const { port } = server.address();

let exit = 1;
const browser = await puppeteer.launch({
  headless: true,
  // CI runners often forbid the user namespaces Chrome's sandbox needs; the only page loaded is our own test page
  ...(process.env.CI ? { args: ["--no-sandbox"] } : {}),
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : { channel: "chrome" }),
});
try {
  const page = await browser.newPage();
  page.on("pageerror", (error) => console.error("page error:", error.message));
  await page.goto(`http://127.0.0.1:${port}/wasm/tests/hosts/chrome.html`);
  await page.waitForFunction(() => window.__result, { timeout: 120_000 });
  const result = await page.evaluate(() => window.__result);
  console.log(result.report);
  exit = result.failures.length === 0 ? 0 : 1;
} finally {
  await browser.close();
  server.close();
}
process.exit(exit);
