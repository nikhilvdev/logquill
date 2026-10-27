import { describe, expect, it } from "vitest";
import {
  OpenAIAgentsAdapter,
  type AgentsSpanLike,
  type AgentsTraceLike,
} from "../../src/adapters/openai-agents-adapter.js";
import { Level } from "../../src/core/levels.js";
import { Logger } from "../../src/core/logger.js";
import { CollectingTransport } from "../../src/transports/transport.js";

// Spans and traces are built to the shapes in `@openai/agents-core`'s `tracing/spans.d.ts` and `traces.d.ts`.
const trace: AgentsTraceLike = { traceId: "trace_abc", name: "Agent workflow", groupId: null };
const span = (spanData: AgentsSpanLike["spanData"], over: Partial<AgentsSpanLike> = {}): AgentsSpanLike => ({
  spanId: "span_1",
  traceId: "trace_abc",
  parentId: null,
  spanData,
  error: null,
  startedAt: "2026-01-01T00:00:00.000Z",
  endedAt: "2026-01-01T00:00:00.250Z",
  ...over,
});

function setup() {
  const sink = new CollectingTransport();
  const logger = new Logger("agents", { level: Level.TRACE, transports: [sink] });
  return { adapter: new OpenAIAgentsAdapter(logger), logger, sink };
}

describe("OpenAIAgentsAdapter", () => {
  it("maps a run: trace, agent span, tool call, generation, handoff", async () => {
    const { adapter, logger, sink } = setup();
    const agent = span({ type: "agent", name: "triage", tools: ["search"], handoffs: ["billing"] }, { spanId: "span_agent" });
    const tool = span({ type: "function", name: "search", input: "{}", output: "ok" }, { spanId: "span_tool", parentId: "span_agent" });
    const generation = span(
      { type: "generation", model: "gpt-5", usage: { input_tokens: 12, output_tokens: 7 } },
      { spanId: "span_gen", parentId: "span_agent" },
    );
    const handoff = span({ type: "handoff", from_agent: "triage", to_agent: "billing" }, { spanId: "span_handoff", parentId: "span_agent" });

    await adapter.onTraceStart(trace);
    await adapter.onSpanStart(agent);
    await adapter.onSpanStart(tool);
    await adapter.onSpanEnd(tool);
    await adapter.onSpanEnd(generation);
    await adapter.onSpanEnd(handoff);
    await adapter.onSpanEnd(agent);
    await adapter.onTraceEnd(trace);
    await logger.flush();

    const byMessage = (message: string) => sink.records.find((r) => r.message === message)!;
    expect(byMessage("call search").meta).toMatchObject({ kind: "action", tool: "search", run_id: "trace_abc", parent_span_id: "span_agent" });
    expect(byMessage("search done").meta).toMatchObject({ kind: "observation", tool: "search", duration_ms: 250 });
    const llm = sink.records.find((r) => r.llm !== undefined)!;
    expect(llm.llm).toMatchObject({ model: "gpt-5", tokens_in: 12, tokens_out: 7, latency_ms: 250 });
    expect(llm.meta).toMatchObject({ span_id: "span_gen", parent_span_id: "span_agent" });
    expect(byMessage("handoff").meta).toMatchObject({ kind: "decision", from_agent: "triage", to_agent: "billing" });
    expect(byMessage("triage").meta).toMatchObject({
      kind: "span",
      operation: "invoke_agent",
      agent_name: "triage",
      span_id: "span_agent",
      duration_ms: 250,
      tools: ["search"],
    });
    expect(byMessage("triage").meta.parent_span_id).toBeUndefined();
    expect(sink.records.every((r) => r.meta.run_id === "trace_abc")).toBe(true);
  });

  it("a response span takes the model and usage from the response", async () => {
    const { adapter, logger, sink } = setup();
    await adapter.onSpanEnd(
      span({ type: "response", response_id: "resp_1", _response: { model: "gpt-5-mini", usage: { input_tokens: 3, output_tokens: 4 } } }),
    );
    await logger.flush();
    expect(sink.records[0]!.llm).toMatchObject({ model: "gpt-5-mini", tokens_in: 3, tokens_out: 4 });
    expect(sink.records[0]!.meta.response_id).toBe("resp_1");
  });

  it("a span with an error is an ERROR record; a triggered guardrail is a WARN", async () => {
    const { adapter, logger, sink } = setup();
    await adapter.onSpanEnd(span({ type: "function", name: "search", input: "{}", output: "" }, { error: { message: "timeout" } }));
    await adapter.onSpanEnd(span({ type: "guardrail", name: "pii", triggered: true }));
    await adapter.onSpanEnd(span({ type: "guardrail", name: "length", triggered: false }));
    await logger.flush();
    expect(sink.records.map((r) => [r.level, r.message])).toEqual([
      ["ERROR", "function failed"],
      ["WARN", "guardrail pii"],
      ["INFO", "guardrail length"],
    ]);
    expect(sink.records[0]!.meta.error).toBe("timeout");
  });

  it("ignores usage that is not a non-negative integer, and span types it has no record for", async () => {
    const { adapter, logger, sink } = setup();
    await adapter.onSpanEnd(span({ type: "generation", model: "m", usage: { input_tokens: -1, output_tokens: "x" } }));
    await adapter.onSpanEnd(span({ type: "transcription", input: { data: "", format: "pcm" } }));
    await logger.flush();
    expect(sink.records).toHaveLength(1);
    expect(sink.records[0]!.llm).toEqual({ model: "m", latency_ms: 250 });
  });

  it("forceFlush and shutdown drain the logger", async () => {
    const { adapter, sink } = setup();
    await adapter.onSpanEnd(span({ type: "custom", name: "note", data: { a: 1 } }));
    await adapter.forceFlush();
    await adapter.shutdown();
    expect(sink.records[0]!.meta).toMatchObject({ a: 1, run_id: "trace_abc" });
  });
});
