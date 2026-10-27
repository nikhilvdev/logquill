import { describe, expect, it } from "vitest";
import { LlamaIndexAdapter, type CallbackManagerLike } from "../../src/adapters/llamaindex-adapter.js";
import { Level } from "../../src/core/levels.js";
import { Logger } from "../../src/core/logger.js";
import { CollectingTransport } from "../../src/transports/transport.js";

/** A stand-in for LlamaIndex.TS's `CallbackManager`: handlers receive a real `CustomEvent`, as it dispatches. */
function fakeManager() {
  const handlers = new Map<string, Set<(event: any) => void>>();
  const manager: CallbackManagerLike & { emit(event: string, detail: unknown): void; count(): number } = {
    on(event, handler) {
      (handlers.get(event) ?? handlers.set(event, new Set()).get(event)!).add(handler);
      return manager;
    },
    off(event, handler) {
      handlers.get(event)?.delete(handler);
      return manager;
    },
    emit(event, detail) {
      for (const handler of handlers.get(event) ?? []) handler(new CustomEvent(event, { detail }));
    },
    count: () => [...handlers.values()].reduce((n, set) => n + set.size, 0),
  };
  return manager;
}

function setup() {
  const sink = new CollectingTransport();
  const logger = new Logger("llama", { level: Level.TRACE, transports: [sink] });
  const manager = fakeManager();
  const adapter = new LlamaIndexAdapter(logger).attach(manager);
  return { adapter, manager, logger, sink };
}

describe("LlamaIndexAdapter", () => {
  it("llm-start then llm-end becomes one llm_call with the model, usage and latency", async () => {
    const { manager, logger, sink } = setup();
    manager.emit("llm-start", { id: "e1", messages: [{ role: "user", content: "hi" }] });
    manager.emit("llm-end", {
      id: "e1",
      response: { message: { role: "assistant", content: "hello" }, raw: { model: "gpt-5", usage: { prompt_tokens: 9, completion_tokens: 4 } } },
    });
    await logger.flush();
    expect(sink.records).toHaveLength(1);
    expect(sink.records[0]!.llm).toMatchObject({ model: "gpt-5", tokens_in: 9, tokens_out: 4 });
    expect(typeof sink.records[0]!.llm!.latency_ms).toBe("number");
    expect(sink.records[0]!.meta.span_id).toBe("e1");
  });

  it("reads other providers' usage names", async () => {
    const { manager, logger, sink } = setup();
    manager.emit("llm-end", { id: "e2", response: { raw: { usage: { input_tokens: 5, output_tokens: 6 } } } });
    await logger.flush();
    expect(sink.records[0]!.llm).toMatchObject({ tokens_in: 5, tokens_out: 6 });
  });

  it("a tool call and its result become an action and an observation sharing the tool call id", async () => {
    const { manager, logger, sink } = setup();
    const toolCall = { id: "call_1", name: "search", input: { q: "x" } };
    manager.emit("llm-tool-call", { toolCall });
    manager.emit("llm-tool-result", { toolCall, toolResult: { isError: false, output: "ok" } });
    await logger.flush();
    expect(sink.records[0]!.meta).toMatchObject({ kind: "action", tool: "search", tool_call_id: "call_1" });
    expect(sink.records[1]!.meta).toMatchObject({ kind: "observation", tool: "search", tool_call_id: "call_1" });
    expect(typeof sink.records[1]!.meta.duration_ms).toBe("number");
  });

  it("a failed tool result is an ERROR record", async () => {
    const { manager, logger, sink } = setup();
    const toolCall = { id: "call_2", name: "search", input: {} };
    manager.emit("llm-tool-call", { toolCall });
    manager.emit("llm-tool-result", { toolCall, toolResult: { isError: true, output: "boom" } });
    await logger.flush();
    expect([sink.records[1]!.level, sink.records[1]!.message]).toEqual(["ERROR", "search failed"]);
  });

  it("detach stops it listening, and attaching again does not double up", async () => {
    const { adapter, manager, logger, sink } = setup();
    expect(manager.count()).toBe(4);
    adapter.attach(manager);
    expect(manager.count()).toBe(4);
    adapter.detach();
    expect(manager.count()).toBe(0);
    manager.emit("llm-end", { id: "e3", response: {} });
    await logger.flush();
    expect(sink.records).toHaveLength(0);
  });

  it("an end event with no start, and a malformed event, do not throw", async () => {
    const { manager, logger, sink } = setup();
    expect(() => {
      manager.emit("llm-end", { response: undefined });
      manager.emit("llm-tool-result", {});
      manager.emit("llm-start", {});
    }).not.toThrow();
    await logger.flush();
    expect(sink.records.length).toBeGreaterThan(0);
  });
});
