// Ported from the Python `test_mcp.py`: trace context propagated through MCP request metadata, and the
// server-side `inbound` block that adopts it and stamps `meta.mcp.*` on every record.
import { describe, expect, it } from "vitest";
import { Logger } from "../../src/core/logger.js";
import type { Plugin } from "../../src/core/plugin.js";
import { inbound, propagate, RUN_ID_KEY, TRACEPARENT_KEY } from "../../src/instrument/mcp.js";
import { RunPlugin } from "../../src/plugins/run-plugin.js";
import { TraceContextPlugin } from "../../src/plugins/trace-context-plugin.js";
import { CollectingTransport } from "../../src/transports/transport.js";

const TRACE = "0af7651916cd43dd8448eb211c80319c";

function setup(extra: Plugin[] = []) {
  const sink = new CollectingTransport();
  const logger = new Logger("mcp", { transports: [sink], plugins: [new TraceContextPlugin(), ...extra] });
  return { logger, sink };
}

describe("propagate", () => {
  it("builds the request metadata a client sends: a W3C traceparent, and the run id if given", () => {
    const meta = propagate({ runId: "run-1" });
    expect(meta[TRACEPARENT_KEY]).toMatch(/^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/);
    expect(meta[RUN_ID_KEY]).toBe("run-1");
  });

  it("uses a given trace id", () => {
    expect(propagate({ traceId: TRACE })[TRACEPARENT_KEY]).toContain(`-${TRACE}-`);
  });

  it("omits the run id when none is given", () => {
    expect(propagate()[RUN_ID_KEY]).toBeUndefined();
  });
});

describe("inbound", () => {
  it("a client's trace id is adopted by every record in the block", async () => {
    const { logger, sink } = setup();
    await inbound(propagate({ traceId: TRACE }), { server: "search", tool: "query" }, () => {
      logger.info("handling");
    });
    await logger.flush();
    expect(sink.records[0]!.meta.trace_id).toBe(TRACE);
  });

  it("stamps meta.mcp.server and meta.mcp.tool on every record in the block", async () => {
    const { logger, sink } = setup();
    await inbound(propagate(), { server: "search", tool: "query" }, async () => {
      logger.info("one");
      await Promise.resolve();
      logger.info("two");
    });
    await logger.flush();
    expect(sink.records.map((r) => r.meta.mcp)).toEqual([
      { server: "search", tool: "query" },
      { server: "search", tool: "query" },
    ]);
  });

  it("with no metadata at all it still stamps mcp, and has no trace to inherit", async () => {
    const { logger, sink } = setup();
    await inbound(undefined, { server: "s", tool: "t" }, () => {
      logger.info("alone");
    });
    await logger.flush();
    expect(sink.records[0]!.meta.mcp).toEqual({ server: "s", tool: "t" });
    expect(sink.records[0]!.meta.trace_id).toMatch(/^[0-9a-f]{32}$/); // a fresh one, not a client's
  });

  it("a client's run id lands under meta.mcp and never shadows the server's own run id", async () => {
    const { logger, sink } = setup([new RunPlugin({ runId: "server-run" })]);
    await inbound(propagate({ runId: "client-run" }), { server: "s", tool: "t" }, () => {
      logger.info("handled");
    });
    await logger.flush();
    expect(sink.records[0]!.meta.run_id).toBe("server-run");
    expect((sink.records[0]!.meta.mcp as Record<string, unknown>).run_id).toBe("client-run");
  });

  it("a malformed traceparent falls back to generating a fresh trace id", async () => {
    const { logger, sink } = setup();
    await inbound({ [TRACEPARENT_KEY]: "not-a-traceparent" }, { server: "s", tool: "t" }, () => {
      logger.info("bad header");
    });
    await logger.flush();
    expect(sink.records[0]!.meta.trace_id).toMatch(/^[0-9a-f]{32}$/);
    expect(sink.records[0]!.meta.trace_id).not.toBe("not-a-traceparent");
  });

  it("restores the previous traceparent after the block, and even if the block raises", async () => {
    const { logger, sink } = setup();
    await inbound(propagate({ traceId: TRACE }), { server: "s", tool: "t" }, () => undefined);
    logger.info("after");
    await expect(
      inbound(propagate({ traceId: TRACE }), { server: "s", tool: "t" }, () => {
        throw new Error("tool failed");
      }),
    ).rejects.toThrow("tool failed");
    logger.info("after the failure");
    await logger.flush();
    for (const record of sink.records) {
      expect(record.meta.trace_id).not.toBe(TRACE);
      expect(record.meta.mcp).toBeUndefined();
    }
  });

  it("returns the block's value", async () => {
    await expect(inbound(undefined, { server: "s", tool: "t" }, () => 42)).resolves.toBe(42);
  });
});
