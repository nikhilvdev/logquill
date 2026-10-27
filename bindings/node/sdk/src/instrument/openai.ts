import type { Logger } from "../core/logger.js";
import type { LlmCallFields } from "../core/records.js";
import { defined, Instrumenter, protoOf } from "./util.js";

/** What `openai()` needs: an instance of the official `openai` client. */
export interface OpenAIInstrumentOptions {
  /** e.g. `new OpenAI()`. The prototypes of its `chat.completions` and `responses` resources are patched, which covers every client. */
  client: { chat: { completions: unknown }; responses: unknown };
}

interface ChatLike {
  model?: string;
  usage?: { prompt_tokens?: number; completion_tokens?: number };
  choices?: { finish_reason?: string | null }[];
}

interface ResponsesLike {
  model?: string;
  status?: string;
  incomplete_details?: { reason?: string | null } | null;
  usage?: { input_tokens?: number; output_tokens?: number };
}

/**
 * `openai.instrument(logger, { client })` patches `chat.completions.create` and
 * `responses.create` so every non-streaming call emits `logger.llmCall()` with
 * the model, token counts, latency and finish reason, and no call-site changes.
 * Streaming calls pass through untouched. `openai.uninstrument()` undoes it.
 * As with `anthropic`, you pass a client rather than logquill importing the SDK.
 */
export const openai = new Instrumenter<OpenAIInstrumentOptions>(
  "openai",
  (logger: Logger, { client }) => [
    {
      target: protoOf(client.chat.completions, "chat.completions"),
      method: "create",
      record: (response, ms) => {
        const chat = response as ChatLike;
        logger.llmCall(
          defined<LlmCallFields>({
            model: chat.model,
            tokensIn: chat.usage?.prompt_tokens,
            tokensOut: chat.usage?.completion_tokens,
            latencyMs: ms,
            finishReason: chat.choices?.[0]?.finish_reason ?? undefined,
          }),
          { provider: "openai" },
        );
      },
    },
    {
      target: protoOf(client.responses, "responses"),
      method: "create",
      record: (response, ms) => {
        const res = response as ResponsesLike;
        logger.llmCall(
          defined<LlmCallFields>({
            model: res.model,
            tokensIn: res.usage?.input_tokens,
            tokensOut: res.usage?.output_tokens,
            latencyMs: ms,
            finishReason: res.incomplete_details?.reason ?? res.status,
          }),
          { provider: "openai" },
        );
      },
    },
  ],
);
