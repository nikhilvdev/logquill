/**
 * Zero-call-site-change instrumentation for LLM SDKs and MCP: a separate entry
 * point (`import ... from "logquill/instrument"`). Nothing here imports an SDK;
 * you pass your own client, so a plain `import { Logger } from "logquill"`
 * never pulls any of it in.
 */
export { anthropic, type AnthropicInstrumentOptions } from "./instrument/anthropic.js";
export { openai, type OpenAIInstrumentOptions } from "./instrument/openai.js";
export { propagate, inbound, TRACEPARENT_KEY, RUN_ID_KEY } from "./instrument/mcp.js";
export { Instrumenter } from "./instrument/util.js";
