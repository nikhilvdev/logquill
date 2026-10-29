// Runs the shared harness inside a Worker, locally under workerd (Cloudflare's open-source runtime), through
// wrangler's programmatic API. Prints the result and exits non-zero on any failure. Nothing is deployed.
import { unstable_dev } from "wrangler";

const worker = await unstable_dev("tests/hosts/worker.mjs", {
  config: "wrangler.toml",
  local: true,
  experimental: { disableExperimentalWarning: true },
});
let exit = 1;
try {
  const result = await (await worker.fetch("/")).json();
  console.log(result.report);
  exit = result.failures.length === 0 ? 0 : 1;
} finally {
  await worker.stop();
}
process.exit(exit);
