"""Trace-context propagation over MCP (Model Context Protocol) requests, plus
stamping `meta.mcp.*` on records logged while handling one.

No dependency on the `mcp` package: both helpers just build or read a plain
`dict[str, str]`, since that is exactly what an MCP client's `meta=` argument
and a server handler's inbound request `_meta` are — the MCP spec's `_meta`
field is a generic metadata bag any request, notification or result may
carry (see the spec's "General fields" / `_meta` section). The client passes
`propagate()`'s result as `meta=` on its call; the server passes the request's
inbound `_meta` (in the Python SDK, `ctx.request_context.meta`) to `inbound()`.

Keys are namespaced under `logquill/`, a plain, unreserved `_meta` prefix —
the spec reserves only prefixes containing a `modelcontextprotocol`/`mcp`
label, and `logquill/traceparent` doesn't.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from logquill.core.context import bind_context
from logquill.plugins.trace_context_plugin import (
    generate_trace_id,
    reset_traceparent,
    set_traceparent,
)

TRACEPARENT_KEY = "logquill/traceparent"
RUN_ID_KEY = "logquill/run-id"


def propagate(*, trace_id: str | None = None, run_id: str | None = None) -> dict[str, str]:
    """Client side: build the `_meta` dict to pass on an outbound MCP request
    so the server shares this call's trace — e.g.

        await session.call_tool("search", args, meta=propagate(run_id=agent_run_id))

    Carries a W3C `traceparent` for `trace_id` (an active one if you don't
    pass one and none is given, else freshly generated — the same resolution
    `TraceContextPlugin` itself uses for an outbound call), so a server whose
    own logger has a `TraceContextPlugin` picks up the *same* `trace_id`.
    `run_id`, if given, rides along too — the server's `inbound()` records it
    under `meta.mcp.run_id`, informationally; it never overrides the server's
    own `RunPlugin` run id, since that would conflate two different runs.
    """
    span_id = generate_trace_id()[:16]
    meta = {TRACEPARENT_KEY: f"00-{trace_id or generate_trace_id()}-{span_id}-01"}
    if run_id:
        meta[RUN_ID_KEY] = run_id
    return meta


@contextmanager
def inbound(meta: Mapping[str, Any] | None, *, server: str, tool: str) -> Iterator[None]:
    """Server side: wraps handling one MCP request (typically a whole tool
    call). `meta` is the inbound request's `_meta` field exactly as the SDK
    hands it to you — in the official Python SDK, `ctx.request_context.meta`
    from inside a tool handler; `None` (no `_meta` sent) is fine.

    For the duration of the block:

    - if `meta` carries a `propagate()`-built `traceparent`, `TraceContextPlugin`
      on any logger used inside resolves to that same `trace_id` — the
      mechanism is `set_traceparent()`, the same one HTTP middleware uses for
      an inbound header.
    - every record logged inside picks up `meta.mcp.server`/`meta.mcp.tool`
      (this call's own `server`/`tool`) and, if the client sent one,
      `meta.mcp.run_id` — via `bind_context()`, so nothing needs passing
      through call signatures by hand.
    """
    meta = meta or {}
    traceparent = meta.get(TRACEPARENT_KEY)
    token = set_traceparent(traceparent if isinstance(traceparent, str) else None)
    mcp_context: dict[str, Any] = {"server": server, "tool": tool}
    run_id = meta.get(RUN_ID_KEY)
    if isinstance(run_id, str) and run_id:
        mcp_context["run_id"] = run_id
    try:
        with bind_context(mcp=mcp_context):
            yield
    finally:
        reset_traceparent(token)
