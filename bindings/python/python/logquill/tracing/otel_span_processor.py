"""`OtelSpanProcessor` — turns live OpenTelemetry spans into LogQuill records.

For frameworks that emit OTel spans directly instead of calling a callback handler. It is duck-typed
against the OTel SDK's `SpanProcessor` shape, so `logquill` never imports `opentelemetry`:

    provider = TracerProvider()
    provider.add_span_processor(OtelSpanProcessor(logger))

Each span becomes an `.action()` record when it starts and an `.observation()` record (or `.error()`
on an error status) when it ends, carrying the span's own `span_id`/`parent_span_id` and, on the end
record, `meta.duration_ms`. Span attributes are copied verbatim onto `meta.attributes`, so a framework
that puts prompt text in attributes puts it in your logs too: filter them with a plugin if that matters.
"""

from __future__ import annotations

from typing import Any

from logquill.core.logger import Logger

_STATUS_ERROR = 2  # opentelemetry.trace.StatusCode.ERROR


def _hex_id(value: Any) -> str | None:
    return format(value, "016x") if isinstance(value, int) and value else None


class OtelSpanProcessor:
    """See the module docstring. `attributes_key` renames the `meta` key the attributes go under."""

    def __init__(self, logger: Logger, attributes_key: str = "attributes") -> None:
        self._log = logger
        self._attributes_key = attributes_key

    def _base_meta(self, span: Any) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        context = getattr(span, "context", None) or span.get_span_context()
        if span_id := _hex_id(getattr(context, "span_id", None)):
            meta["span_id"] = span_id
        if parent_id := _hex_id(getattr(getattr(span, "parent", None), "span_id", None)):
            meta["parent_span_id"] = parent_id
        if attributes := getattr(span, "attributes", None):
            meta[self._attributes_key] = dict(attributes)
        return meta

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        """Logs the span's start as an `.action()`."""
        self._log.action(span.name, **self._base_meta(span))

    def on_end(self, span: Any) -> None:
        """Logs the span's end as an `.observation()`, or an `.error()` if its status is ERROR."""
        meta = self._base_meta(span)
        start, end = getattr(span, "start_time", None), getattr(span, "end_time", None)
        if start is not None and end is not None:
            meta["duration_ms"] = (end - start) / 1e6
        status = getattr(span, "status", None)
        code = getattr(getattr(status, "status_code", None), "value", None)
        if code == _STATUS_ERROR:
            meta["error"] = getattr(status, "description", None) or "span ended with an error status"
            self._log.error(span.name, **meta)
        else:
            self._log.observation(span.name, **meta)

    def shutdown(self) -> None:
        """Nothing to release: the logger owns its transports."""

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Nothing is buffered here; call `logger.flush()` to drain the logger."""
        return True
