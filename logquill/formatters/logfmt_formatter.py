from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from logquill.core.records import LogRecord

#: The core record fields, always emitted first. A `meta` key with one of
#: these names is emitted as `meta.<key>` so it can't shadow the field the
#: record itself carries.
_RESERVED_KEYS = ("timestamp", "level", "logger", "message")

#: Nested `meta` dicts are flattened into dotted keys (`http.status=200`) up
#: to this depth; anything deeper is emitted as one JSON string, so a
#: pathologically nested (or circular) value can't blow up the line.
_MAX_FLATTEN_DEPTH = 5

_CONTROL = "\\x00-\\x1f\\x7f\\x85\\u2028\\u2029"
_NEEDS_QUOTES = re.compile(rf'[\s"=\\{_CONTROL}]|^$')
_BAD_KEY_CHARS = re.compile(rf'[\s"=\\{_CONTROL}]')
_CONTROL_CHAR = re.compile(f"[{_CONTROL}]")
_ESCAPES = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _quote(text: str) -> str:
    if not _NEEDS_QUOTES.search(text):
        return text
    escaped = "".join(
        _ESCAPES.get(char) or (f"\\u{ord(char):04x}" if _CONTROL_CHAR.match(char) else char)
        for char in text
    )
    return f'"{escaped}"'


def _key(raw: object) -> str:
    return _BAD_KEY_CHARS.sub("_", str(raw)) or "_"


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, (list, tuple, dict)):
        return _quote(_json(value))
    return _quote(_safe_str(value))


def _json(value: Any) -> str:
    try:
        return json.dumps(value, separators=(",", ":"), default=str)
    except Exception:
        return _safe_str(value)


def _safe_str(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return f"<unrepresentable {type(value).__name__}>"


def _flatten(prefix: str, value: Any, depth: int, out: list[str]) -> None:
    if isinstance(value, Mapping) and value and depth < _MAX_FLATTEN_DEPTH:
        for child_key, child in value.items():
            _flatten(f"{prefix}.{_key(child_key)}", child, depth + 1, out)
        return
    out.append(f"{prefix}={_scalar(value)}")


class LogfmtFormatter:
    """Single-line `key=value` output — the logfmt convention popularized by
    Heroku and Go's logging ecosystem — for teams whose downstream tooling
    (Loki, Splunk, `grep`) expects it:

        timestamp=2026-01-01T00:00:00.000Z level=INFO logger=app message="user signed up" user_id=42

    An LLM call's `llm` block follows as `llm.model=...`, `llm.tokens_in=...`.
    `meta` keys are emitted as top-level pairs after the four record fields;
    nested dicts flatten to dotted keys (`http.status=200`); lists are
    emitted as a JSON string. Values containing whitespace, `=`, quotes, or
    control characters are double-quoted and escaped, so the output is
    always exactly one line — a multi-line traceback in `meta.stack` becomes
    a single quoted value with `\\n` escapes. `logquill.parse_logfmt` reads
    the format back.

    Never raises on odd `meta` values; non-serializable objects degrade to
    their `str()`.
    """

    def format(self, record: LogRecord) -> str:
        """Renders `record` as one logfmt line."""
        pairs = [
            f"timestamp={_quote(str(record['timestamp']))}",
            f"level={_quote(str(record['level']))}",
            f"logger={_quote(str(record['logger']))}",
            f"message={_quote(str(record['message']))}",
        ]
        llm = record.get("llm")
        if llm:
            _flatten("llm", llm, 1, pairs)
        for meta_key, value in record["meta"].items():
            key = _key(meta_key)
            if key in _RESERVED_KEYS:
                key = f"meta.{key}"
            _flatten(key, value, 1, pairs)
        return " ".join(pairs)
