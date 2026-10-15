from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from logquill.core.levels import Level

if TYPE_CHECKING:
    from logquill.core.logger import Logger

# Attributes every stdlib `logging.LogRecord` carries for its own bookkeeping
# (formatting, source location, timing) — excluded from the `meta` a bridged
# record produces so `meta` only ever holds what a caller passed as `extra=`,
# the same shape a native LogQuill call would produce.
_STDLIB_RECORD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
    }
)


def _map_level(levelno: int) -> Level:
    """Stdlib `logging` levels map onto LogQuill's directly except `WARNING`
    (30 in both, but named `WARN` here) and `CRITICAL` (50, named `FATAL`
    here) — see the cross-language level contract in CLAUDE.md. Anything
    that doesn't land exactly on a defined level (a custom intermediate
    level, or `NOTSET`) rounds down to the nearest one, the same way stdlib
    `logging` itself treats level thresholds as "at least this severe."
    """
    if levelno >= logging.CRITICAL:
        return Level.FATAL
    if levelno >= logging.ERROR:
        return Level.ERROR
    if levelno >= logging.WARNING:
        return Level.WARN
    if levelno >= logging.INFO:
        return Level.INFO
    if levelno >= logging.DEBUG:
        return Level.DEBUG
    return Level.TRACE


class LogQuillHandler(logging.Handler):
    """Bridges stdlib `logging` calls into a LogQuill `Logger`, so output
    from third-party libraries (or code not yet migrated off `logging`)
    flows through the same transports and plugin pipeline as LogQuill's own
    `.info()`/`.error()`/... calls, instead of needing every call site
    rewritten:

        handler = LogQuillHandler(logger)
        logging.getLogger().addHandler(handler)

        logging.getLogger("some.library").warning("retrying", extra={"attempt": 2})
        # -> logger.warn("retrying", attempt=2) via the same transports/plugins

    Any `extra=` fields passed to the stdlib call land in `meta` exactly
    like keyword args to a native LogQuill call would; an attached
    `exc_info` is formatted into `meta["stack"]` the same way `Logger`'s own
    `exc_info=` kwarg is. Level filtering still goes through the wrapped
    `Logger`'s own `set_level()` (via its ordinary `_log` path), on top of
    whatever this handler's or the stdlib logger's own level is set to.
    """

    def __init__(self, logger: Logger, level: int = logging.NOTSET) -> None:
        """`logger` is the LogQuill `Logger` every bridged stdlib record is
        forwarded onto; `level` is this handler's own stdlib-level filter,
        applied on top of `logger`'s own level."""
        super().__init__(level)
        self._logger = logger

    def emit(self, record: logging.LogRecord) -> None:
        """Translates a stdlib `logging.LogRecord` into a LogQuill call:
        maps its level, folds any `extra=` fields into `meta`, formats
        `exc_info` if present, and routes it through the wrapped `Logger`'s
        own `_log` path. Delegates to `self.handleError()` (never raises)
        if translation itself fails."""
        try:
            meta: dict[str, Any] = {
                key: value
                for key, value in record.__dict__.items()
                if key not in _STDLIB_RECORD_ATTRS and key != "message"
            }
            if record.exc_info:
                meta["exc_info"] = record.exc_info
            self._logger._log(_map_level(record.levelno), record.getMessage(), meta)
        except Exception:
            self.handleError(record)
