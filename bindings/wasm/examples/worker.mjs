import wasm from "@logquill/wasm/logquill_wasm_bg.wasm";
import { initWasm, Logger, CollectingTransport, RedactPlugin } from "@logquill/wasm";

export default {
  async fetch() {
    await initWasm(wasm); // a Worker cannot fetch or compile WebAssembly, so it is imported precompiled

    const sink = new CollectingTransport();
    const log = new Logger("edge", { transports: [sink], plugins: [new RedactPlugin()] });
    log.info("request", { token: "abc123", path: "/" });
    await log.flush();

    return Response.json(sink.records[0].meta);
  },
};
