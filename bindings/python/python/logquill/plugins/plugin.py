from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Any, Callable

from logquill.core.records import LogRecord

MiddlewareFunc = Callable[[LogRecord], "LogRecord | None"]


class Plugin:
    """Base class for the plugin pipeline: `before_log`, `after_log`, `on_error`.

    Override only the hooks you need — the rest default to no-ops. A plugin
    hook that raises cannot crash logging: the pipeline catches it, routes it
    to `on_error`, and moves on.

    A plugin that protects data (redaction) sets `fails_closed = True`: if its `before_log`
    raises, the record is dropped rather than passed on unprotected (decision D20).
    """

    #: Whether a failure of `before_log` drops the record instead of skipping this plugin.
    fails_closed: bool = False

    #: A native hash chain, for the one plugin that chains the log (`TamperEvidentPlugin`). The
    #: dispatcher stamps it at write time, in queue order (decision D22).
    _chain: object | None = None

    def before_log(self, record: LogRecord) -> LogRecord | None:
        """Return a (possibly modified) record, or `None` to drop it."""
        return record

    def after_log(self, record: LogRecord) -> None:
        """Called after the record has been dispatched to every transport."""

    def on_error(self, exc: Exception, record: LogRecord) -> None:
        """Called when one of this plugin's own hooks raises."""

    def redact_local(self, name: str, text: str) -> str:
        """Optional hook for redaction plugins: called with the name and
        `repr` of each local variable that `diagnose=True` captures into a
        traceback, before the traceback is formatted, and returns the text to
        show instead. Defaults to returning `text` unchanged. Only override
        this if your plugin masks sensitive values — see `RedactPlugin`. If
        it raises, the value is replaced with a placeholder rather than shown.
        """
        return text


class FunctionPlugin(Plugin):
    """Wraps a plain `before_log`-style function as a `Plugin`.

    `Logger.use()` builds one of these automatically when given a function
    instead of a `Plugin` subclass — a one-off transform shouldn't require
    subclassing ceremony. There's no `next()` chaining: the pipeline is
    already an ordered list of hooks the `Logger` calls in sequence, so this
    is sugar for a single-method `Plugin`, not a new execution model.
    """

    def __init__(self, func: MiddlewareFunc) -> None:
        """`func` is the plain `before_log`-style function this instance
        delegates to."""
        self._func = func

    def before_log(self, record: LogRecord) -> LogRecord | None:
        """Delegates to the wrapped function."""
        return self._func(record)


_logger = logging.getLogger("logquill")


def write_released(transports: Sequence[Any] | None, records: Iterable[LogRecord]) -> None:
    """Write records a plugin has released (a sampled-out trace that turned out to matter, a run's
    buffered DEBUG trail, a run summary) straight to `transports`, bypassing the later plugins. A
    transport that fails is reported and does not stop the others or the logger."""
    for record in records:
        for transport in transports or ():
            try:
                transport.write(transport.format(record), record)
            except Exception:
                _logger.exception("%s: failed to write a released record", type(transport).__name__)
