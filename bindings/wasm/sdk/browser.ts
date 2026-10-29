// The browser SDK on the WASM core: the Node SDK's own browser entry (logger, formatters, context, redaction, PII,
// sampling, console and beacon transports, stack-based spans), plus the loader and the plugins that only exist
// because there is a Rust core to run them. Built by `npm run build:sdk` with `core/native.ts` swapped for
// `native-wasm.ts`, so redaction and PII scanning run in Rust once `initWasm()` has finished, and in JavaScript before.
//
//   import { initWasm, Logger, ConsoleTransport, RedactPlugin } from "@logquill/wasm";
//   await initWasm();
//   const log = new Logger("web", { transports: [new ConsoleTransport()], plugins: [new RedactPlugin()] });
export * from "../../node/sdk/src/browser.js";

export { AdaptiveSamplingPlugin, FlightRecorderPlugin, RunSummaryPlugin } from "../../node/sdk/src/plugins/native-plugins.js";
export type {
  AdaptiveSamplingPluginOptions,
  FlightRecorderPluginOptions,
  RunSummaryPluginOptions,
} from "../../node/sdk/src/plugins/native-plugins.js";

export { initWasm, initWasmSync, wasmReady } from "./native-wasm.js";
