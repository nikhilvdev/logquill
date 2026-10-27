from __future__ import annotations

from typing import Any

from logquill import _native
from logquill.core.records import LogRecord

#: The only module that knows Elastic Common Schema (ECS) field names — `ElasticsearchTransport` is
#: the only caller, same "isolate the convention behind a thin mapping layer" principle as the
#: OpenTelemetry attribute names. The mapping itself is in the Rust core, shared with the other
#: languages.


def to_ecs_document(record: LogRecord) -> dict[str, Any]:
    """Returns a copy of `record` with Elastic Common Schema fields added alongside LogQuill's own —
    never replacing them, since ECS is explicitly permissive about extra fields riding alongside
    the standard ones. Getting the *standard* fields named correctly is what makes Kibana's default
    Discover view, dashboards, and alerting rules work on this data with zero manual field mapping:

    - `@timestamp` from `timestamp`
    - `log.level` from `level`, lowercased (ECS convention)
    - `service.name` from `logger`
    - `trace.id` / `span.id` from `meta.trace_id` / `meta.span_id`, when present
    - `error.message` / `error.stack_trace`, when `meta.stack` is present (from `exc_info=` or
      `diagnose=`); `error.message` reuses the record's own `message`, since LogQuill doesn't keep
      the exception's message separately from the formatted traceback
    """
    return _native.to_ecs_document(record)  # type: ignore[no-any-return]
