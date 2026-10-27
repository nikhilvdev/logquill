from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
_SPAN_ID = re.compile(r"^[0-9a-f]{16}$")


def _digest(text: str, nbytes: int) -> int:
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:nbytes], "big")


def span_id_for(value: str) -> int:
    """A 64-bit OpenTelemetry span id for a LogQuill span id: the id itself if
    it already is 16 hex characters (the shape `Logger.span()` generates),
    otherwise a stable hash of it — LangChain's run ids are UUIDs, for example.
    The same input always gives the same id, so a child's `parent_span_id` and
    its parent's own `span_id` still meet."""
    if _SPAN_ID.match(value) and int(value, 16) != 0:
        return int(value, 16)
    return _digest(value, 8) or 1


def trace_id_for(meta: Mapping[str, Any]) -> int | None:
    """The 128-bit trace id a record belongs to: `meta.trace_id` if it's a valid
    32-hex id (what `TraceContextPlugin` writes), else derived from
    `meta.run_id` so every span of one agent run shares a trace. `None` if the
    record names neither."""
    trace_id = meta.get("trace_id")
    if isinstance(trace_id, str) and _TRACE_ID.match(trace_id) and int(trace_id, 16) != 0:
        return int(trace_id, 16)
    run_id = meta.get("run_id")
    if isinstance(run_id, str) and run_id:
        return _digest("run:" + run_id, 16) or 1
    return None


def trace_id_from_parent(parent_span_id: str) -> int:
    """A trace id for spans that name a parent but no run or trace: derived from
    the parent's id, so siblings share a trace."""
    return _digest("parent:" + parent_span_id, 16) or 1
