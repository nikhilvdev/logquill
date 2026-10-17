from __future__ import annotations

import json
from typing import Callable, Literal

from logquill.core.records import LogRecord

_Dumps = Callable[[LogRecord], str]


def _json_dumps(record: LogRecord) -> str:
    return json.dumps(record, separators=(",", ":"), default=str)


def _make_orjson_dumps() -> _Dumps:
    try:
        import orjson
    except ImportError as exc:
        raise ImportError(
            "JSONFormatter(backend='orjson') requires the optional `orjson` "
            "dependency — install with `pip install logquill[fast]`."
        ) from exc

    def dumps(record: LogRecord) -> str:
        # Explicit annotation, not inlined: when `orjson` has no stubs on
        # mypy's search path (the pre-commit hook's bare environment
        # doesn't install it), `orjson.dumps(...)` types as `Any` and
        # returning `.decode(...)` on it directly would trip
        # `--strict`'s `warn_return_any` — annotating `encoded: bytes`
        # tells mypy to trust that type from here on instead.
        encoded: bytes = orjson.dumps(record, default=str)
        return encoded.decode("utf-8")

    return dumps


class JSONFormatter:
    """Serializes a record to the canonical JSON line shape.

    `backend="json"` (default) uses the stdlib — zero dependencies, same as
    every other core component. `backend="orjson"` (`pip install
    logquill[fast]`) uses the Rust-backed `orjson` library instead, which is
    meaningfully faster on the hot path under sustained logging volume; the
    output is the same compact JSON either way, so switching backends never
    changes what a transport receives. Raises `ImportError` right away, at
    construction, if `orjson` isn't installed — not on the first `format()`
    call.
    """

    def __init__(self, *, backend: Literal["json", "orjson"] = "json") -> None:
        if backend == "json":
            self._dumps: _Dumps = _json_dumps
        elif backend == "orjson":
            self._dumps = _make_orjson_dumps()
        else:
            raise ValueError(f"JSONFormatter: backend must be 'json' or 'orjson', got {backend!r}")

    def format(self, record: LogRecord) -> str:
        """Serializes `record` to a single compact JSON line; non-JSON-native
        values fall back to `str()` rather than raising."""
        return self._dumps(record)
