from __future__ import annotations

import re
import secrets
from contextvars import ContextVar, Token

from logquill.core.records import LogRecord
from logquill.plugins.plugin import Plugin

_current_traceparent: ContextVar[str | None] = ContextVar("logquill_traceparent", default=None)

# W3C Trace Context: "{version}-{trace-id}-{parent-id}-{trace-flags}",
# https://www.w3.org/TR/trace-context/#traceparent-header
_W3C_TRACEPARENT_RE = re.compile(
    r"^[0-9a-f]{2}-(?P<trace_id>[0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$"
)
# AWS X-Ray: "Root=1-{8 hex}-{24 hex}[;Parent=...;Sampled=...]"
_XRAY_ROOT_RE = re.compile(r"Root=1-(?P<time>[0-9a-f]{8})-(?P<rand>[0-9a-f]{24})")
# GCP Cloud Trace: "{32 hex trace id}/{decimal span id}[;o=TRACE_TRUE]"
_GCP_TRACE_RE = re.compile(r"^(?P<trace_id>[0-9a-f]{32})/\d+(;o=\d)?$")


def set_traceparent(value: str | None) -> Token[str | None]:
    """Set the inbound trace header for the current execution context (e.g.
    request-scoped HTTP middleware, before the handler runs). Backed by a
    `contextvars.ContextVar`, so it's isolated per thread/asyncio task —
    concurrent requests never see each other's header. Returns a token;
    pass it to `reset_traceparent` to restore the previous value (typically
    in a `finally` block once the request is done).
    """
    return _current_traceparent.set(value)


def reset_traceparent(token: Token[str | None]) -> None:
    """Restore the trace header context to what it was before the matching
    `set_traceparent` call, using the token that call returned."""
    _current_traceparent.reset(token)


def generate_trace_id() -> str:
    """A fresh 32-hex-char id, matching the shape of an OTel/W3C trace id."""
    return secrets.token_hex(16)


def parse_trace_header(header: str) -> str | None:
    """Extract a 32-hex-char trace id from a W3C `traceparent`, AWS X-Ray
    `X-Amzn-Trace-Id`, or GCP `X-Cloud-Trace-Context` header value. Returns
    `None` if `header` doesn't match any of the three shapes.
    """
    header = header.strip()

    match = _W3C_TRACEPARENT_RE.match(header)
    if match:
        return match.group("trace_id")

    match = _XRAY_ROOT_RE.search(header)
    if match:
        return match.group("time") + match.group("rand")

    match = _GCP_TRACE_RE.match(header)
    if match:
        return match.group("trace_id")

    return None


class TraceContextPlugin(Plugin):
    """Stamps `meta.trace_id` for cross-service correlation — distinct from
    `RunPlugin`'s `run_id`: `trace_id` follows one request across services,
    `run_id` scopes one agent run.

    A record that already carries `meta[trace_key]` (e.g. because
    `SamplingPlugin`'s tail-based elevation, or an upstream plugin, already
    set one) is left alone. Otherwise resolves a trace id in priority order:

    1. An active OpenTelemetry span's trace id, if the `opentelemetry-api`
       package is importable and a span is current — read directly via
       `opentelemetry.trace.get_current_span()`, not just inbound headers.
       Import is lazy and best-effort: `TraceContextPlugin` never requires
       OpenTelemetry as a dependency.
    2. The `traceparent` constructor argument, if given.
    3. Whatever `set_traceparent()` most recently set for the current
       thread/asyncio task — the propagation mechanism framework middleware
       (Flask/FastAPI/etc.) uses to hand this plugin an inbound header
       without threading it through every log call.
    4. A freshly generated trace id, if none of the above produced one.

    Header parsing understands W3C `traceparent`, AWS X-Ray
    `X-Amzn-Trace-Id`, and GCP `X-Cloud-Trace-Context` — see
    `parse_trace_header`. A header that doesn't parse is treated the same
    as no header: falls through to generating a new trace id.
    """

    def __init__(self, *, trace_key: str = "trace_id", traceparent: str | None = None) -> None:
        """`traceparent`, if given, takes priority over whatever
        `set_traceparent()` set for the current context — see the class
        docstring's resolution order."""
        self.trace_key = trace_key
        self._explicit_traceparent = traceparent

    def before_log(self, record: LogRecord) -> LogRecord | None:
        """Stamps `meta[trace_key]` if not already set, resolving a trace id
        per the class docstring's priority order."""
        meta = record["meta"]
        if meta.get(self.trace_key) is not None:
            return record
        meta[self.trace_key] = self._resolve_trace_id()
        return record

    def _resolve_trace_id(self) -> str:
        from_otel = self._from_active_otel_span()
        if from_otel is not None:
            return from_otel

        header = self._explicit_traceparent or _current_traceparent.get()
        if header is not None:
            parsed = parse_trace_header(header)
            if parsed is not None:
                return parsed

        return generate_trace_id()

    @staticmethod
    def _from_active_otel_span() -> str | None:
        try:
            from opentelemetry import trace as otel_trace
        except ImportError:
            return None
        span = otel_trace.get_current_span()
        span_context = span.get_span_context()
        if not span_context.is_valid:
            return None
        return format(span_context.trace_id, "032x")
