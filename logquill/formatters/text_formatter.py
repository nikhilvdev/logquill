from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from logquill.core.records import LogRecord


def format_text(record: Mapping[str, Any]) -> str:
    """Render a record as one human-readable entry:

        2026-01-01T00:00:00.000Z INFO  app.api: user signed up {"user_id":42}

    Tolerant of partial records (a missing field renders as `?`), which is
    what lets the `logquill tail` CLI share this with `TextFormatter` for
    log lines written by other tools. A formatted traceback in
    `meta["stack"]` is printed on the lines after the entry, as a
    traceback normally reads, instead of as one long escaped JSON string.
    """
    meta = dict(record.get("meta") or {})
    stack = meta.get("stack")
    if isinstance(stack, str) and stack:
        del meta["stack"]
    else:
        stack = None

    line = (
        f"{record.get('timestamp', '?')} {str(record.get('level', '?')):<5} "
        f"{record.get('logger', '?')}: {record.get('message', '')}"
    )
    llm = record.get("llm")
    if llm:
        line += f" llm={_dump_meta(llm)}"
    if meta:
        line += f" {_dump_meta(meta)}"
    if stack is not None:
        line += "\n" + stack.rstrip("\n")
    return line


def _dump_meta(meta: Mapping[str, Any]) -> str:
    try:
        return json.dumps(meta, separators=(",", ":"), default=str)
    except Exception:
        # e.g. a circular reference or a value whose `__str__` raises: keep
        # the entry readable rather than let a log call's formatting fail
        return json.dumps({key: _safe_repr(value) for key, value in meta.items()}, default=str)


def _safe_repr(value: Any) -> str:
    try:
        return repr(value)[:200]
    except Exception:
        return f"<unrepresentable {type(value).__name__}>"


class TextFormatter:
    """Human-readable single-entry output for local development and
    terminals — `ConsoleTransport(formatter=TextFormatter())`.

    Use `JSONFormatter` (the default) for anything a machine will read; this
    one is for eyes. It never raises on odd `meta` values (non-serializable
    objects, circular references) — they degrade to their `repr()`.
    """

    def format(self, record: LogRecord) -> str:
        """Renders `record` via `format_text`."""
        return format_text(record)
