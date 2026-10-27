"""Bridges from other Python logging libraries into a LogQuill `Logger`, next to the stdlib
`LogQuillHandler`: neither library is imported here, so `import logquill` stays dependency-free.

    structlog.configure(processors=[..., structlog_processor(log)])   # last in the chain
    loguru.logger.add(loguru_sink(log))
"""

from __future__ import annotations

from typing import Any, Callable

from logquill.core.handler import _map_level
from logquill.core.logger import Logger

_STRUCTLOG_LEVELS = {"warning": 30, "warn": 30, "exception": 40, "error": 40, "critical": 50, "fatal": 50}
_STRUCTLOG_BOOKKEEPING = ("event", "level", "timestamp")  # added by processors; the record has its own


def structlog_processor(logger: Logger) -> Callable[[Any, str, dict[str, Any]], dict[str, Any]]:
    """A structlog processor that forwards each event to `logger` and then drops it, so put it last:
    nothing is printed twice. `event` becomes the message, the other keys become `meta`, and an
    `exc_info` is formatted into `meta.stack` like a native call's. Needs `structlog` installed
    (it is, if you are using this)."""

    def process(_: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        import structlog  # lazy: the bridge is only built where structlog already exists

        meta = {k: v for k, v in event_dict.items() if k not in _STRUCTLOG_BOOKKEEPING}
        level = _map_level(_STRUCTLOG_LEVELS.get(method_name, 20))
        logger._log(level, str(event_dict.get("event", "")), meta)
        raise structlog.DropEvent

    return process


def loguru_sink(logger: Logger) -> Callable[[Any], None]:
    """A loguru sink for `loguru.logger.add(...)`. Loguru levels share LogQuill's numeric weights
    (`SUCCESS`, 25, rounds down to `INFO`); `bind()`/`extra` values become `meta`, and a caught
    exception is formatted into `meta.stack`."""

    def sink(message: Any) -> None:
        record = message.record
        meta = dict(record["extra"])
        exception = record["exception"]
        if exception is not None and exception.value is not None:
            meta["exc_info"] = exception.value
        logger._log(_map_level(record["level"].no), record["message"], meta)

    return sink
