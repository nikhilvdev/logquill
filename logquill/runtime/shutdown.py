from __future__ import annotations

import atexit
import logging
import threading
import weakref
from typing import TYPE_CHECKING

from logquill.runtime.worker import AsyncWorker
from logquill.transports.transport import Transport

if TYPE_CHECKING:
    from logquill.core.logger import Logger

_logger = logging.getLogger("logquill")

#: How long the exit hook waits for queued records to drain before giving up
#: and letting the process exit anyway — a stalled sink must not be able to
#: hang shutdown forever.
EXIT_DRAIN_TIMEOUT_SECONDS = 5.0

# Weak, so registering never keeps a logger (or its transports) alive.
_loggers: weakref.WeakSet[Logger] = weakref.WeakSet()
_closed_transports: weakref.WeakSet[Transport] = weakref.WeakSet()
_lock = threading.Lock()
_hook_installed = False


def register(logger: Logger) -> None:
    """Have the process-exit hook flush and close `logger`'s worker and
    transports if the process ends without `logger.close()` having been
    called. Installs the single `atexit` hook on first use."""
    global _hook_installed
    with _lock:
        _loggers.add(logger)
        if not _hook_installed:
            atexit.register(shutdown)
            _hook_installed = True


def mark_closed(transport: Transport) -> None:
    """Record that `transport` was closed explicitly, so the exit hook
    doesn't close it a second time (a child logger shares its parent's
    transports)."""
    with _lock:
        _closed_transports.add(transport)


def shutdown(timeout: float = EXIT_DRAIN_TIMEOUT_SECONDS) -> None:
    """Drain every registered logger's async queue (up to `timeout`
    seconds), then close each transport once. Runs automatically at
    interpreter exit; safe to call earlier, and safe to call twice.

    Never raises: anything that goes wrong is reported on the `logquill`
    stdlib logger, because a failing flush must not turn a clean exit into
    a traceback.
    """
    with _lock:
        loggers = list(_loggers)
        already_closed = set(_closed_transports)

    workers: dict[int, AsyncWorker] = {}
    transports: dict[int, Transport] = {}
    for logger in loggers:
        if logger._worker is not None:
            workers[id(logger._worker)] = logger._worker
        for transport in logger.transports:
            if transport not in already_closed:
                transports[id(transport)] = transport

    for worker in workers.values():
        try:
            if not worker.close(timeout):
                _logger.warning(
                    "logquill: %.1fs wasn't enough to flush every queued record at exit — "
                    "a transport is slow or unreachable; call logger.close(timeout=...) "
                    "yourself with a longer timeout if these records matter",
                    timeout,
                )
        except Exception:
            _logger.exception("logquill: failed to drain the async queue at exit")

    for transport in transports.values():
        try:
            transport.close()
        except Exception:
            _logger.exception("%s: failed to close at exit", type(transport).__name__)
        mark_closed(transport)
