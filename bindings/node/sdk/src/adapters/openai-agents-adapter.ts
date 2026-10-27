import type { Logger } from "../core/logger.js";

// The OpenAI Agents SDK for JavaScript (`@openai/agents`) reports a run as a *trace* made of *spans*, to every
// registered `TracingProcessor`. This adapter is such a processor, duck-typed against the interface in
// `@openai/agents-core`'s `tracing/processor.d.ts`, so `logquill` never imports the SDK. Register it with
//
//     import { addTraceProcessor } from "@openai/agents";
//     addTraceProcessor(new OpenAIAgentsAdapter(logger));
//
// (`setTraceProcessors([...])` replaces the default exporter to OpenAI's backend instead of adding to it.)

/** The part of the SDK's `Span` this adapter reads. */
export interface AgentsSpanLike {
  spanId: string;
  traceId: string;
  parentId: string | null;
  spanData: { type: string; [key: string]: any };
  error: { message: string; data?: Record<string, unknown> } | null;
  startedAt: string | null;
  endedAt: string | null;
}

/** The part of the SDK's `Trace` this adapter reads. */
export interface AgentsTraceLike {
  traceId: string;
  name: string;
  groupId: string | null;
  metadata?: Record<string, unknown> | undefined;
}

function durationMs(span: AgentsSpanLike): number | undefined {
  if (span.startedAt === null || span.endedAt === null) return undefined;
  const ms = Date.parse(span.endedAt) - Date.parse(span.startedAt);
  return Number.isFinite(ms) && ms >= 0 ? Math.round(ms * 1000) / 1000 : undefined;
}

const count = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : undefined;

function tokens(input: number | undefined, output: number | undefined): Record<string, number> {
  return {
    ...(input === undefined ? {} : { tokensIn: input }),
    ...(output === undefined ? {} : { tokensOut: output }),
  };
}

/**
 * Turns the OpenAI Agents SDK's tracing into LogQuill records:
 *
 * | span                       | record                                                                    |
 * |----------------------------|---------------------------------------------------------------------------|
 * | `agent`                    | a span record named for the agent, `operation: "invoke_agent"`           |
 * | `function` (a tool call)   | `.action(tool=...)` when it starts, `.observation(tool=...)` when it ends |
 * | `generation`, `response`   | `.llmCall()` with the model, token usage and elapsed time                |
 * | `handoff`                  | `.decision()` naming `from_agent` and `to_agent`                         |
 * | `guardrail`                | a WARN record if it was triggered, otherwise INFO                        |
 * | `custom`                   | an INFO record carrying the span's data                                  |
 * | any span with an `error`   | an ERROR record                                                          |
 *
 * The SDK's own span and trace ids become `meta.span_id`, `meta.parent_span_id` and `meta.run_id`, so the run
 * tree reconstructs from the log with no extra plumbing.
 */
export class OpenAIAgentsAdapter {
  private readonly log: Logger;

  constructor(agentLog: Logger) {
    this.log = agentLog;
  }

  start(): void {}

  async onTraceStart(trace: AgentsTraceLike): Promise<void> {
    this.log.info(`trace ${trace.name}`, { kind: "trace", run_id: trace.traceId });
  }

  async onTraceEnd(trace: AgentsTraceLike): Promise<void> {
    this.log.info(`trace ${trace.name} done`, { kind: "trace", run_id: trace.traceId });
  }

  async onSpanStart(span: AgentsSpanLike): Promise<void> {
    const data = span.spanData;
    if (data.type === "function") {
      this.log.action(`call ${String(data.name)}`, {
        tool: data.name,
        ...this.ids(span),
      });
    }
  }

  async onSpanEnd(span: AgentsSpanLike): Promise<void> {
    const data = span.spanData;
    const ids = this.ids(span);
    const duration = durationMs(span);
    const timing = duration === undefined ? {} : { duration_ms: duration };

    if (span.error !== null) {
      this.log.error(`${data.type} failed`, {
        error: span.error.message,
        ...(data.name === undefined ? {} : { name: data.name }),
        ...ids,
        ...timing,
      });
      return;
    }

    switch (data.type) {
      case "agent":
        this.log.info(String(data.name ?? "agent"), {
          kind: "span",
          operation: "invoke_agent",
          agent_name: data.name,
          ...(Array.isArray(data.tools) ? { tools: data.tools } : {}),
          ...(Array.isArray(data.handoffs) ? { handoffs: data.handoffs } : {}),
          ...ids,
          ...timing,
        });
        break;
      case "function":
        this.log.observation(`${String(data.name)} done`, { tool: data.name, ...ids, ...timing });
        break;
      case "generation": {
        const usage = data.usage ?? {};
        this.log.llmCall(
          {
            ...(typeof data.model === "string" ? { model: data.model } : {}),
            ...tokens(count(usage.input_tokens), count(usage.output_tokens)),
            ...(duration === undefined ? {} : { latencyMs: duration }),
          },
          ids,
        );
        break;
      }
      case "response": {
        const response = data._response ?? {};
        const usage = response.usage ?? {};
        this.log.llmCall(
          {
            ...(typeof response.model === "string" ? { model: response.model } : {}),
            ...tokens(count(usage.input_tokens), count(usage.output_tokens)),
            ...(duration === undefined ? {} : { latencyMs: duration }),
          },
          { ...ids, ...(typeof data.response_id === "string" ? { response_id: data.response_id } : {}) },
        );
        break;
      }
      case "handoff":
        this.log.decision("handoff", { from_agent: data.from_agent, to_agent: data.to_agent, ...ids });
        break;
      case "guardrail":
        (data.triggered ? this.log.warn : this.log.info).call(this.log, `guardrail ${String(data.name)}`, {
          triggered: Boolean(data.triggered),
          ...ids,
          ...timing,
        });
        break;
      case "custom":
        this.log.info(String(data.name ?? "custom"), { ...(data.data ?? {}), ...ids, ...timing });
        break;
      default:
        // task, turn, transcription, speech, mcp_tools, ...: nothing an agent log needs beyond its timing
        break;
    }
  }

  async shutdown(): Promise<void> {
    await this.log.flush();
  }

  async forceFlush(): Promise<void> {
    await this.log.flush();
  }

  private ids(span: AgentsSpanLike): Record<string, unknown> {
    return {
      run_id: span.traceId,
      span_id: span.spanId,
      ...(span.parentId === null ? {} : { parent_span_id: span.parentId }),
    };
  }
}
