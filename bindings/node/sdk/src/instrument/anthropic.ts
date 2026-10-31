import type { Logger } from "../core/logger.js";
import type { LlmCallFields } from "../core/records.js";
import { defined, Instrumenter, protoOf } from "./util.js";

/** What `anthropic()` needs: an instance of the official `@anthropic-ai/sdk` client. */
export interface AnthropicInstrumentOptions {
  /** e.g. `new Anthropic()`. Its `messages` resource's prototype is patched, which covers every client. */
  client: { messages: unknown };
}

interface MessageLike {
  model?: string;
  stop_reason?: string | null;
  usage?: { input_tokens?: number; output_tokens?: number };
}

/**
 * `anthropic.instrument(logger, { client })` patches `messages.create` (and so
 * `messages.parse`) so every non-streaming call emits `logger.llmCall()` with
 * the model, token counts, latency and stop reason, and no call-site changes.
 * Streaming calls pass through untouched. `anthropic.uninstrument()` undoes it.
 *
 * The SDK is never imported by logquill: you hand over a client, so the copy
 * you patch is the copy your app actually calls (ESM and CommonJS builds of
 * the SDK are different modules with different prototypes).
 */
export const anthropic = new Instrumenter<AnthropicInstrumentOptions>(
  "anthropic",
  (logger: Logger, { client }) => [
    {
      target: protoOf(client.messages, "messages"),
      method: "create",
      record: (response, ms) => {
        const message = response as MessageLike;
        logger.llmCall(
          defined<LlmCallFields>({
            model: message.model,
            tokensIn: message.usage?.input_tokens,
            tokensOut: message.usage?.output_tokens,
            latencyMs: ms,
            finishReason: message.stop_reason ?? undefined,
          }),
          { provider: "anthropic" },
        );
      },
    },
  ],
);
