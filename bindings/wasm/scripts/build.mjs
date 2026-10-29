// Builds the module: cargo for wasm32-unknown-unknown, then wasm-bindgen (`--target web`) into pkg/.
// The wasm-bindgen CLI has to be the exact version the crate pins; a mismatch gives a module that
// fails to load with an unhelpful message, so it is checked here first.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const run = (cmd, args) => execFileSync(cmd, args, { cwd: root, stdio: "inherit" });

const pinned = /wasm-bindgen = "=([\d.]+)"/.exec(readFileSync(`${root}Cargo.toml`, "utf8"))?.[1];
let installed = "";
try {
  installed = execFileSync("wasm-bindgen", ["--version"], { encoding: "utf8" }).trim().split(" ")[1];
} catch {
  // handled below
}
if (installed !== pinned) {
  console.error(
    `wasm-bindgen CLI ${installed || "not found"}, but Cargo.toml pins ${pinned}. ` +
      `Install the matching one: cargo install wasm-bindgen-cli --version ${pinned} --locked`,
  );
  process.exit(1);
}

run("cargo", ["build", "--target", "wasm32-unknown-unknown", "--release"]);
run("wasm-bindgen", [
  "target/wasm32-unknown-unknown/release/logquill_wasm.wasm",
  "--target",
  "web",
  "--out-dir",
  "pkg",
  "--out-name",
  "logquill_wasm",
  "--no-typescript",
  // debugging aids that a page downloads and never uses: about 200 KB of the module, 28 KB gzipped
  "--remove-name-section",
  "--remove-producers-section",
]);
console.log("built pkg/logquill_wasm.js and pkg/logquill_wasm_bg.wasm");
