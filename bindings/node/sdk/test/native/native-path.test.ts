import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { nativeAddon } from "../../src/core/native.js";
import { Logger } from "../../src/core/logger.js";
import { FileTransport } from "../../src/transports/file-transport.js";

describe.skipIf(!nativeAddon()?.Dispatcher)("the Rust file transport behind a Logger", () => {
  it("is the one in use, and writes off the main thread", async () => {
    const path = join(mkdtempSync(join(tmpdir(), "lq-native-")), "a.log");
    const file = new FileTransport(path);
    expect(file.nativeHandle).toBeDefined();
    const logger = new Logger("app", { transports: [file] });
    for (let i = 0; i < 1000; i++) logger.info("m", { i });
    await logger.close();
    const lines = readFileSync(path, "utf8").trim().split("\n");
    expect(lines).toHaveLength(1000);
    expect(JSON.parse(lines[999]!).meta.i).toBe(999);
  });

  it("falls back to the JavaScript transport for a custom formatter", () => {
    const path = join(mkdtempSync(join(tmpdir(), "lq-native-")), "b.log");
    const file = new FileTransport(path, { formatter: { format: () => "x" } });
    expect(file.nativeHandle).toBeUndefined();
    file.close();
  });

  it("survives a record the JSON serializer rejects", async () => {
    const path = join(mkdtempSync(join(tmpdir(), "lq-native-")), "c.log");
    const logger = new Logger("app", { transports: [new FileTransport(path)] });
    const loop: Record<string, unknown> = {};
    loop.self = loop;
    logger.info("cyclic", { loop });
    logger.info("fine");
    await logger.close();
    expect(readFileSync(path, "utf8")).toContain('"message":"fine"');
  });
});
