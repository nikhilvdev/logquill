import { initWasm, Logger, ConsoleTransport, RedactPlugin, PIIRedactPlugin } from "@logquill/wasm";

await initWasm(); // Deno and Bun can fetch a file: URL; on Node pass the bytes: initWasm(readFileSync(path))

const log = new Logger("server", {
  transports: [new ConsoleTransport({ colorize: false })],
  plugins: [new RedactPlugin(), new PIIRedactPlugin()],
});
log.info("signed in", { user: "alice@example.com", password: "hunter2" });
await log.flush();
