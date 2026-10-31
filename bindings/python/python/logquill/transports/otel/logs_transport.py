from __future__ import annotations

import json
from typing import Any

from logquill import _native
from logquill.core.records import LogRecord
from logquill.formatters import Formatter
from logquill.tracing import semconv
from logquill.transports.otel import _env, _ids
from logquill.transports.transport import Transport

_SEVERITY = {
    "TRACE": "TRACE",
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "WARN": "WARN",
    "ERROR": "ERROR",
    "FATAL": "FATAL",
}


def _attribute_value(value: Any) -> Any:
    """An OpenTelemetry attribute value: scalars and homogeneous lists of them
    pass through; anything else (a nested dict, a mixed list, an object) becomes
    a JSON string, since attributes can't hold structure."""
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)) and value:
        kinds = {type(item) for item in value}
        if len(kinds) == 1 and kinds <= {str, bool, int, float}:
            return list(value)
    try:
        return json.dumps(value, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


class OTelLogsTransport(Transport):
    """Sends every LogQuill record to an OpenTelemetry collector as an OTLP
    *log record*, without going through the span path — for a backend that
    takes logs, or to ship the records `OTLPTransport` doesn't turn into spans.

    The message is the body, the level maps to OTLP severity, and `meta`
    becomes attributes (nested values as JSON strings; a traceback in
    `meta.stack` goes to `exception.stacktrace`). A record with a `trace_id` or
    `run_id`, or a `span_id`, carries the same ids `OTLPTransport` gives its
    spans, so a collector can join a log line to its span. An `llm` block adds
    the standard model and token attributes.

    Requires `pip install logquill[otel]`, imported lazily.
    """

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        headers: dict[str, str] | None = None,
        service_name: str = "logquill",
        log_exporter: Any = None,
        processor: str = "batch",
        semconv_version: str | None = None,
        timeout: float = 10.0,
        formatter: Formatter | None = None,
    ) -> None:
        """Arguments mirror `OTLPTransport`: `endpoint` is the OTLP/HTTP logs
        URL, `log_exporter` swaps in any OpenTelemetry log exporter, and
        `processor` is `"batch"` or `"simple"`."""
        super().__init__(formatter)
        if processor not in ("batch", "simple"):
            raise ValueError(f"processor must be 'batch' or 'simple', got {processor!r}")
        if log_exporter is None:
            # The default is done in Rust (OTLP/HTTP JSON, no OpenTelemetry SDK needed). Passing
            # your own `log_exporter` uses the SDK.
            self._native = _native.otlp_logs(
                _env.endpoint(endpoint, "logs"),
                _env.headers(headers, "logs"),
                service_name,
                semconv_version,
                "unknown",
                False,
                1 if processor == "simple" else 100,
                1_000_000,
                timeout,
            )
            self._native_dispatch = self._native
            return
        try:
            from opentelemetry.sdk._logs import LoggerProvider
            from opentelemetry.sdk._logs.export import (
                BatchLogRecordProcessor,
                SimpleLogRecordProcessor,
            )
            from opentelemetry.sdk.resources import Resource
        except ImportError as exc:
            raise ImportError(
                "OTelLogsTransport requires the OpenTelemetry SDK — install with "
                "`pip install logquill[otel]`."
            ) from exc
        if processor not in ("batch", "simple"):
            raise ValueError(f"processor must be 'batch' or 'simple', got {processor!r}")
        if log_exporter is None:
            try:
                from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
            except ImportError as exc:
                raise ImportError(
                    "OTelLogsTransport needs the OTLP/HTTP exporter — install with "
                    "`pip install logquill[otel]`."
                ) from exc
            log_exporter = OTLPLogExporter(endpoint=endpoint, headers=headers, timeout=timeout)

        self._convention = semconv.resolve_convention(semconv_version)
        self._provider = LoggerProvider(resource=Resource.create({"service.name": service_name}))
        self._provider.add_log_record_processor(
            BatchLogRecordProcessor(log_exporter)
            if processor == "batch"
            else SimpleLogRecordProcessor(log_exporter)
        )
        self._logger = self._provider.get_logger("logquill")

    def write(self, formatted: str, record: LogRecord) -> None:
        """Emits `record` as an OTLP log record."""
        if self._native is not None:
            self._native.write(formatted, record)
            return
        from opentelemetry._logs import LogRecord as OTelLogRecord
        from opentelemetry._logs import SeverityNumber

        meta = record["meta"]
        attributes: dict[str, Any] = {}
        for key, value in meta.items():
            if key == "stack":
                attributes["exception.stacktrace"] = _attribute_value(value)
            elif value is not None:
                attributes[key] = _attribute_value(value)
        attributes["logquill.logger"] = record["logger"]
        attributes["logquill.schema_version"] = record["schema_version"]
        attributes.update(semconv.log_attributes(record, self._convention))

        span_id = meta.get("span_id")
        timestamp = semconv.epoch_ns(record["timestamp"])
        self._logger.emit(
            OTelLogRecord(
                timestamp=timestamp,
                observed_timestamp=timestamp,
                trace_id=_ids.trace_id_for(meta),
                span_id=_ids.span_id_for(span_id) if isinstance(span_id, str) and span_id else None,
                severity_text=record["level"],
                severity_number=getattr(SeverityNumber, _SEVERITY[record["level"]]),
                body=record["message"],
                attributes=attributes,
            )
        )

    def flush(self) -> None:
        """Exports every log record still waiting in the batch processor."""
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
