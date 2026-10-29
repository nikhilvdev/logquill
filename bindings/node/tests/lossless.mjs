// Reads the shared fixtures from disk, losslessly. The parsing itself is in lossless-core.mjs, which has no
// Node built-ins, so the browser, Deno and Worker harnesses use it too.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { parseLossless } from "./lossless-core.mjs";

export { LOSSLESS, parseLossless, plain, toText } from "./lossless-core.mjs";

export const SCHEMA = fileURLToPath(new URL("../../../schema/", import.meta.url));

export function readLossless(path) {
  return parseLossless(readFileSync(path, "utf8"));
}
