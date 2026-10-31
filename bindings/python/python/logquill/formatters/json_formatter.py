from __future__ import annotations

from typing import Literal

from logquill import _native
from logquill.core.records import LogRecord


class JSONFormatter:
    """Serializes a record to the canonical JSON line shape, in Rust.

    Output is compact JSON, UTF-8 (not `\\uXXXX`-escaped, decision D32), with non-JSON-native
    values written with `str()` and NaN/infinity as `null`, so formatting never raises over odd
    `meta`. `backend` is accepted for compatibility with 2.x (`"json"` and `"orjson"` both use the
    Rust formatter now; `logquill[fast]` is no longer needed).
    """

    def __init__(self, *, backend: Literal["json", "orjson"] = "json") -> None:
        if backend not in ("json", "orjson"):
            raise ValueError(f"JSONFormatter: backend must be 'json' or 'orjson', got {backend!r}")

    def format(self, record: LogRecord) -> str:
        """Serializes `record` to a single compact JSON line."""
        return _native.format_json(record)
