from __future__ import annotations

import logging
import threading
from typing import Any

from logquill.core.records import LogRecord
from logquill.formatters import Formatter
from logquill.tracing import semconv
from logquill import _native
from logquill.transports.otel import _env, _ids
from logquill.transports.transport import Transport

_logger = logging.getLogger("logquill")


def _require_otel() -> None:
    try:
        import opentelemetry.sdk.trace  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "OTLPTransport requires the OpenTelemetry SDK — install with "
            "`pip install logquill[otel]`."
        ) from exc


class OTLPTransport(Transport):
    """Exports LogQuill's agent tracing as real OpenTelemetry spans.

    A log line and a span are different things, so this doesn't format text: it
    reads each record's structure and, for the records that describe work with a
    beginning and an end, creates an OpenTelemetry span through the SDK, so it
    goes through the usual processors and exporters to any OTLP collector
    (Jaeger, Tempo, Honeycomb, Datadog, Grafana, ...):

    - a `Logger.span()` block becomes a span. One that marks itself as an agent
      run — `logger.span("run", operation="invoke_agent", agent_name="planner")` —
      is `invoke_agent planner`; any other keeps its own name.
    - an `.action()` naming its tool in `meta.tool` becomes `execute_tool {tool}`.
    - a record with an `llm` block (`Logger.llm_call()`) becomes `chat {model}`
      with the model, token counts and finish reason as the standard GenAI
      attributes.

    Every other record is ignored here; send those to a collector too with
    `OTelLogsTransport`. The span's real ids, start and end are taken from the
    record (`span_id`, `parent_span_id`, `duration_ms`, the timestamp), so the
    tree a collector shows is exactly the tree that was logged. Spans of one run
    share a trace when the records carry a `run_id` or `trace_id`.

    Attribute names come from `logquill.semconv`, pinned to one release of the
    (still experimental) GenAI conventions; see `semconv` for choosing the
    naming generation. Prompt and completion text is never exported unless you
    turn on `capture_content`.

    Requires `pip install logquill[otel]`, imported lazily. Export is batched
    and happens off the caller's thread; `flush()`/`close()` send what's pending.
    """

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        headers: dict[str, str] | None = None,
        service_name: str = "logquill",
        span_exporter: Any = None,
        processor: str = "batch",
        semconv_version: str | None = None,
        default_provider: str = semconv.UNKNOWN_PROVIDER,
        capture_content: bool | None = None,
        timeout: float = 10.0,
        formatter: Formatter | None = None,
    ) -> None:
        """`endpoint` is the OTLP/HTTP traces URL (default: the SDK's, i.e.
        `OTEL_EXPORTER_OTLP_*` environment variables, else localhost).
        `span_exporter` replaces the OTLP exporter with any OpenTelemetry
        `SpanExporter` — for tests, or a different protocol; `processor` is
        `"batch"` (default) or `"simple"` (export each span immediately).
        `semconv_version` is `"latest"` or `"legacy"`; unset, it follows
        `OTEL_SEMCONV_STABILITY_OPT_IN`. `default_provider` names the provider
        for records without `meta.provider`. `capture_content` defaults to the
        `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` environment
        variable, else off."""
        super().__init__(formatter)
        if processor not in ("batch", "simple"):
            raise ValueError(f"processor must be 'batch' or 'simple', got {processor!r}")
        if span_exporter is None:
            # The default is done in Rust: OTLP/HTTP with JSON encoding, the span mapping from the
            # shared core, no OpenTelemetry SDK needed. Passing your own `span_exporter` uses the SDK.
            capture = (
                semconv.capture_content_enabled() if capture_content is None else capture_content
            )
            self._native = _native.otlp_traces(
                _env.endpoint(endpoint, "traces"),
                _env.headers(headers, "traces"),
                service_name,
                semconv_version,
                default_provider,
                capture,
                1 if processor == "simple" else 100,
                1_000_000,
                timeout,
            )
            self._native_dispatch = self._native
            return
        _require_otel()
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
        from opentelemetry.sdk.trace.id_generator import RandomIdGenerator

        self._convention = semconv.resolve_convention(semconv_version)
        self._default_provider = default_provider
        self._capture_content = (
            semconv.capture_content_enabled() if capture_content is None else capture_content
        )

        pending = threading.local()

        class _RecordIds(RandomIdGenerator):  # type: ignore[misc]
            """Uses the ids the record being exported names, when it names any."""

            def generate_span_id(self) -> int:
                return int(getattr(pending, "span_id", None) or super().generate_span_id())

            def generate_trace_id(self) -> int:
                return int(getattr(pending, "trace_id", None) or super().generate_trace_id())

        self._pending = pending
        if span_exporter is None:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            except ImportError as exc:
                raise ImportError(
                    "OTLPTransport needs the OTLP/HTTP exporter — install with "
                    "`pip install logquill[otel]`."
                ) from exc
            span_exporter = OTLPSpanExporter(endpoint=endpoint, headers=headers, timeout=timeout)

        self._provider = TracerProvider(
            resource=Resource.create({"service.name": service_name}),
            id_generator=_RecordIds(),
        )
        span_processor = (
            BatchSpanProcessor(span_exporter)
            if processor == "batch"
            else SimpleSpanProcessor(span_exporter)
        )
        self._provider.add_span_processor(span_processor)
        self._tracer = self._provider.get_tracer("logquill")

    def _spec_for(self, record: LogRecord) -> semconv.SpanSpec | None:
        if semconv.is_llm_record(record):
            return semconv.llm_spec(
                record,
                self._convention,
                default_provider=self._default_provider,
                capture_content=self._capture_content,
            )
        if semconv.is_tool_record(record):
            return semconv.tool_spec(record, self._convention)
        if semconv.is_span_record(record):
            return semconv.span_spec(
                record, self._convention, default_provider=self._default_provider
            )
        return None

    def write(self, formatted: str, record: LogRecord) -> None:
        """Creates and ends the span this record describes, if it describes one
        (see the class docstring); ignores the record otherwise."""
        if self._native is not None:
            self._native.write(formatted, record)
            return
        spec = self._spec_for(record)
        if spec is None:
            return

        from opentelemetry import trace
        from opentelemetry.trace import (
            NonRecordingSpan,
            SpanContext,
            SpanKind,
            Status,
            StatusCode,
            TraceFlags,
        )

        meta = record["meta"]
        end_ns = semconv.epoch_ns(record["timestamp"])
        start_ns = end_ns - int(spec.duration_ms * 1_000_000) if spec.duration_ms else end_ns

        own = meta.get("span_id")
        parent = meta.get("parent_span_id")
        trace_id = _ids.trace_id_for(meta)
        parent_context = None
        if isinstance(parent, str) and parent:
            if trace_id is None:
                trace_id = _ids.trace_id_from_parent(parent)
            parent_context = trace.set_span_in_context(
                NonRecordingSpan(
                    SpanContext(
                        trace_id=trace_id,
                        span_id=_ids.span_id_for(parent),
                        is_remote=True,
                        trace_flags=TraceFlags(TraceFlags.SAMPLED),
                    )
                )
            )

        self._pending.trace_id = trace_id
        self._pending.span_id = _ids.span_id_for(own) if isinstance(own, str) and own else None
        try:
            span = self._tracer.start_span(
                spec.name,
                context=parent_context,
                kind=SpanKind.CLIENT if spec.kind == "client" else SpanKind.INTERNAL,
                attributes=spec.attributes,
                start_time=start_ns,
            )
        finally:
            self._pending.trace_id = self._pending.span_id = None
        for name, attributes in spec.events:
            span.add_event(name, attributes, timestamp=end_ns)
        if spec.is_error:
            description = meta.get("error")
            span.set_status(
                Status(StatusCode.ERROR, description if isinstance(description, str) else None)
            )
        span.end(end_time=end_ns)

    def flush(self) -> None:
        """Exports every span still waiting in the batch processor."""
        if self._native is not None:
            self._native.flush()
            return
        self._provider.force_flush()

    def close(self) -> None:
        """Flushes, then shuts the exporter down."""
        if self._native is not None:
            self._native.close()
            return
        self._provider.shutdown()
