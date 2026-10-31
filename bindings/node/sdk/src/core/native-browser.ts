// The browser build's stand-in for `native.ts`: there is no native addon in a browser, so every
// caller takes its pure-JavaScript path. Selected by the `"browser"` condition of the `#native`
// subpath import in package.json, the same way `#span` and `#context` are.
import type { NativeAddon, NativeDispatcher } from "./native.js";

export function nativeAddon(): NativeAddon | null {
  return null;
}

export function drainOnExit(_dispatcher: NativeDispatcher): void {}
