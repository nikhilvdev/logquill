// The browser SDK's stand-in for the Node SDK's `core/native.ts`. There the "native addon" is a napi module; here it
// is the WASM core (`js/index.mjs`), which has the same names and shapes for everything that does not need the
// worker, files or sockets. `tsup.config.ts` swaps this file in for `core/native.ts` (and for the `#native` import),
// so `RedactPlugin`, `PIIRedactPlugin` and the Rust-backed plugins use the core without a line changed in them.
//
// Until `initWasm()` has finished, `nativeAddon()` is `null` and every plugin takes its pure-JavaScript path. A plugin
// decides when it is constructed, so call `await initWasm()` before building a logger whose plugins should use Rust.
import type { NativeAddon, NativeDispatcher } from "../../node/sdk/src/core/native.js";
import * as wasm from "../js/index.mjs";

export type { NativeAddon, NativeDispatcher, NativeTransport } from "../../node/sdk/src/core/native.js";

let core: NativeAddon | null = null;

/** The WASM core once `initWasm()` has run, else `null` (callers keep a pure-JavaScript path for that). */
export function nativeAddon(): NativeAddon | null {
  return core;
}

/** Whether the WASM core is loaded, so plugins built from now on use it. */
export function wasmReady(): boolean {
  return core !== null;
}

/**
 * Loads the WASM core. `source` is where the `.wasm` is: a URL or `Response` (a page fetches it), bytes, or a
 * `WebAssembly.Module` (Workers import it precompiled). Omit it to fetch `logquill_wasm_bg.wasm` from beside this
 * bundle. Calling it again is harmless.
 */
export async function initWasm(source?: Parameters<typeof wasm.init>[0]): Promise<void> {
  if (core !== null) return;
  await wasm.init(source);
  core = wasm as unknown as NativeAddon;
}

/** The synchronous form, for hosts that can compile at load time (Node, Deno, Bun, a Worker with a precompiled module). */
export function initWasmSync(source: Parameters<typeof wasm.initSync>[0]): void {
  if (core !== null) return;
  wasm.initSync(source);
  core = wasm as unknown as NativeAddon;
}

/** `object` without its `undefined` fields (the Node SDK's helper, unchanged). */
export function compact<T extends object>(object: T): { [K in keyof T]?: Exclude<T[K], undefined> } {
  return Object.fromEntries(Object.entries(object).filter(([, value]) => value !== undefined)) as {
    [K in keyof T]?: Exclude<T[K], undefined>;
  };
}

/** The core for a feature with no JavaScript fallback; throws, saying what to do, when it is not loaded. */
export function requireNative(feature: string, member?: keyof NativeAddon): NativeAddon {
  if (core === null) {
    throw new Error(`${feature} needs the WebAssembly core: call \`await initWasm()\` before creating it.`);
  }
  if (member !== undefined && core[member] === undefined) {
    throw new Error(`${feature} needs the native runtime (worker thread, files, sockets), which the browser build does not include.`);
  }
  return core;
}

/** Nothing to drain: the browser build has no Rust queue. */
export function drainOnExit(_dispatcher: NativeDispatcher): void {}
