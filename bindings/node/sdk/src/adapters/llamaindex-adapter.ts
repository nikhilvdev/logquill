import type { Logger } from "../core/logger.js";

// LlamaIndex.TS (`llamaindex`, `@llamaindex/core`) reports what an LLM does through a `CallbackManager`:
// `Settings.callbackManager.on("llm-start", handler)`. A handler receives a `CustomEvent`, whose `detail` is
// `{ id, messages }` for `llm-start`, `{ id, response }` for `llm-end`, `{ toolCall }` for `llm-tool-call` and
// `{ toolCall, toolResult }` for `llm-tool-result` (`@llamaindex/core`'s `global/dist/index.d.ts`). This adapter
// registers on those four events and is duck-typed against `on` / `off`, so `logquill` never imports LlamaIndex:
//
//     import { Settings } from "llamaindex";
//     new LlamaIndexAdapter(logger).attach(Settings.callbackManager);

/** The part of LlamaIndex.TS's `CallbackManager` this adapter uses. */
export interface CallbackManagerLike {
  on(event: string, handler: (event: any) => void): unknown;
  off(event: string, handler: (event: any) => void): unknown;
}

/** Most in-flight LLM calls and tool calls remembered; if an end event never arrives the oldest start is forgotten. */
const MAX_PENDING = 1000;

function remember(map: Map<string, number>, key: string): void {
  if (map.size >= MAX_PENDING) {
    const oldest = map.keys().next();
    if (!oldest.done) map.delete(oldest.value);
  }
  map.set(key, performance.now());
}

function take(map: Map<string, number>, key: string): number | undefined {
  const start = map.get(key);
  if (start === undefined) return undefined;
  map.delete(key);
  return Math.round((performance.now() - start) * 1000) / 1000;
}

const count = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : undefined;

/** Pulls the model and token counts out of a response's `raw` payload, whatever the provider called them. */
function usageOf(response: any): { model?: string; tokensIn?: number; tokensOut?: number } {
  const raw = response?.raw;
  const usage = raw?.usage ?? raw?.usageMetadata ?? {};
  const tokensIn = count(usage.prompt_tokens ?? usage.input_tokens ?? usage.promptTokenCount);
  const tokensOut = count(usage.completion_tokens ?? usage.output_tokens ?? usage.candidatesTokenCount);
  const model = typeof raw?.model === "string" ? raw.model : undefined;
  return {
    ...(model === undefined ? {} : { model }),
    ...(tokensIn === undefined ? {} : { tokensIn }),
    ...(tokensOut === undefined ? {} : { tokensOut }),
  };
}

/**
 * Turns LlamaIndex.TS's LLM events into LogQuill records:
 *
 * | event             | record                                                                  |
 * |-------------------|-------------------------------------------------------------------------|
 * | `llm-end`         | `.llmCall()` with the model, token usage and the time since `llm-start`  |
 * | `llm-tool-call`   | `.action(tool=...)`                                                      |
 * | `llm-tool-result` | `.observation(tool=...)` with the elapsed time, or an ERROR if it failed |
 *
 * The event's own `id` becomes `meta.span_id` and a tool call's `id` becomes `meta.tool_call_id`.
 */
export class LlamaIndexAdapter {
  private readonly log: Logger;
  private readonly llmStarts = new Map<string, number>();
  private readonly toolStarts = new Map<string, number>();
  private manager: CallbackManagerLike | undefined;
  private readonly handlers: [string, (event: any) => void][] = [
    ["llm-start", (event) => this.onLlmStart(detail(event))],
    ["llm-end", (event) => this.onLlmEnd(detail(event))],
    ["llm-tool-call", (event) => this.onToolCall(detail(event))],
    ["llm-tool-result", (event) => this.onToolResult(detail(event))],
  ];

  constructor(agentLog: Logger) {
    this.log = agentLog;
  }

  /** Starts listening on `manager` (normally `Settings.callbackManager`). Returns `this`. */
  attach(manager: CallbackManagerLike): this {
    this.detach();
    for (const [name, handler] of this.handlers) manager.on(name, handler);
    this.manager = manager;
    return this;
  }

  /** Stops listening. Safe to call when not attached. */
  detach(): void {
    if (this.manager === undefined) return;
    for (const [name, handler] of this.handlers) this.manager.off(name, handler);
    this.manager = undefined;
  }

  private onLlmStart(d: { id?: string }): void {
    if (typeof d.id === "string") remember(this.llmStarts, d.id);
  }

  private onLlmEnd(d: { id?: string; response?: unknown }): void {
    const id = typeof d.id === "string" ? d.id : undefined;
    const latency = id === undefined ? undefined : take(this.llmStarts, id);
    this.log.llmCall(
      { ...usageOf(d.response), ...(latency === undefined ? {} : { latencyMs: latency }) },
      id === undefined ? {} : { span_id: id },
    );
  }

  private onToolCall(d: { toolCall?: { id?: string; name?: string } }): void {
    const call = d.toolCall ?? {};
    const name = call.name ?? "tool";
    if (typeof call.id === "string") remember(this.toolStarts, call.id);
    this.log.action(`call ${name}`, { tool: name, ...(call.id === undefined ? {} : { tool_call_id: call.id }) });
  }

  private onToolResult(d: { toolCall?: { id?: string; name?: string }; toolResult?: { isError?: boolean } }): void {
    const call = d.toolCall ?? {};
    const name = call.name ?? "tool";
    const duration = typeof call.id === "string" ? take(this.toolStarts, call.id) : undefined;
    const meta = {
      tool: name,
      ...(call.id === undefined ? {} : { tool_call_id: call.id }),
      ...(duration === undefined ? {} : { duration_ms: duration }),
    };
    if (d.toolResult?.isError === true) this.log.error(`${name} failed`, meta);
    else this.log.observation(`${name} done`, meta);
  }
}

function detail(event: any): any {
  return event?.detail ?? event ?? {};
}
