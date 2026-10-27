import { bindContext } from "../core/context.js";
import { generateTraceId, withTraceparent } from "../plugins/trace-context-plugin.js";

/** `_meta` key carrying the W3C `traceparent`. Unreserved: the MCP spec reserves only prefixes with an `mcp` label. */
export const TRACEPARENT_KEY = "logquill/traceparent";
/** `_meta` key carrying the client's agent `run_id`, recorded informationally on the server. */
export const RUN_ID_KEY = "logquill/run-id";

/**
 * Client side: builds the `_meta` object to pass on an outbound MCP request so
 * the server shares this call's trace, e.g.
 * `client.callTool({ name, arguments, _meta: propagate({ runId }) })`.
 *
 * Carries a W3C `traceparent` for `traceId` (freshly generated if you pass
 * none), so a server whose logger has a `TraceContextPlugin` picks up the same
 * `trace_id`. `runId` rides along too; the server records it as
 * `meta.mcp.run_id` and never lets it override its own `RunPlugin` run id,
 * which would conflate two different runs.
 *
 * No `@modelcontextprotocol/sdk` import: `_meta` is a plain string-keyed bag on
 * every MCP request, so this returns exactly that.
 */
export function propagate(
  options: { traceId?: string; runId?: string } = {},
): Record<string, string> {
  const spanId = generateTraceId().slice(0, 16);
  const meta: Record<string, string> = {
    [TRACEPARENT_KEY]: `00-${options.traceId ?? generateTraceId()}-${spanId}-01`,
  };
  if (options.runId) meta[RUN_ID_KEY] = options.runId;
  return meta;
}

/**
 * Server side: runs `fn` (typically one whole tool call) with the inbound
 * request's `_meta` applied. Pass `_meta` exactly as the SDK hands it to the
 * handler (`extra._meta`); `undefined` (nothing sent) is fine.
 *
 * Inside `fn`, `TraceContextPlugin` on any logger resolves to the client's
 * `trace_id`, and every record carries `meta.mcp.server`, `meta.mcp.tool` and,
 * if the client sent one, `meta.mcp.run_id`. Returns `fn`'s result unchanged.
 */
export async function inbound<T>(
  meta: Record<string, unknown> | undefined,
  options: { server: string; tool: string },
  fn: () => T | Promise<T>,
): Promise<T> {
  const traceparent = meta?.[TRACEPARENT_KEY];
  const mcp: Record<string, unknown> = { server: options.server, tool: options.tool };
  const runId = meta?.[RUN_ID_KEY];
  if (typeof runId === "string" && runId) mcp.run_id = runId;
  // scoped to the block, so the caller's own trace id is back as soon as it ends (see `withTraceparent`)
  return await withTraceparent(typeof traceparent === "string" ? traceparent : undefined, () =>
    bindContext({ mcp }, fn),
  );
}
