from __future__ import annotations

from typing import Any

from logquill.core.records import LogRecord

#: The only module that knows Elastic Common Schema (ECS) field names —
#: `ElasticsearchTransport` is the only caller, same "isolate the
#: convention behind a thin mapping layer" principle as
#: `logquill.tracing.semconv` for OTel GenAI attributes.


def to_ecs_document(record: LogRecord) -> dict[str, Any]:
    """Returns a copy of `record` with Elastic Common Schema fields added
    alongside LogQuill's own — never replacing them, since ECS is
    explicitly permissive about extra fields riding alongside the
    standard ones. Getting the *standard* fields named correctly is what
    makes Kibana's default Discover view, dashboards, and alerting rules
    work on this data with zero manual field mapping:

    - `@timestamp` from `timestamp`
    - `log.level` from `level`, lowercased (ECS convention)
    - `service.name` from `logger`
    - `trace.id` / `span.id` from `meta.trace_id` / `meta.span_id`, when present
    - `error.message` / `error.stack_trace`, when `meta.stack` is present
      (from `exc_info=`/`diagnose=`) — `error.message` reuses the record's
      own `message`, since LogQuill doesn't keep the exception's message
      separately from the formatted traceback
    """
    meta = record.get("meta", {})
    document: dict[str, Any] = dict(record)
    document["@timestamp"] = record["timestamp"]
    document["log"] = {"level": record["level"].lower()}
    document["service"] = {"name": record["logger"]}

    trace_id = meta.get("trace_id")
    if trace_id is not None:
        document["trace"] = {"id": trace_id}

    span_id = meta.get("span_id")
    if span_id is not None:
        document["span"] = {"id": span_id}

    stack = meta.get("stack")
    if isinstance(stack, str):
        document["error"] = {"message": record["message"], "stack_trace": stack}

    return document
