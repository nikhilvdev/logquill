from __future__ import annotations

import atexit
import logging
import os
import signal
import sys
import threading
import weakref
from typing import TYPE_CHECKING

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


def _flush_before_fork() -> None:
    """A fork taken while a transport is mid-write leaves its lock held forever in the child, and the
    core can't repair that (it forbids `unsafe`). So the host drains first: every live dispatcher is
    flushed, bounded so a stalled sink can't hang `os.fork()`."""
    with _lock:
        loggers = list(_loggers)
    for dispatcher in {id(x._dispatcher): x._dispatcher for x in loggers if x._dispatcher is not None}.values():
        try:
            dispatcher.flush(EXIT_DRAIN_TIMEOUT_SECONDS)  # type: ignore[attr-defined]
        except Exception:
            _logger.exception("logquill: failed to flush before fork")


if hasattr(os, "register_at_fork"):
    os.register_at_fork(before=_flush_before_fork)


def install_signal_handlers(
    logger: Logger,
    signals: tuple[int, ...] = (signal.SIGTERM, signal.SIGINT),
    timeout: float = EXIT_DRAIN_TIMEOUT_SECONDS,
) -> None:
    """Opt in to a clean stop on `SIGTERM` (a container stop) and `SIGINT`: close `logger`, which
    drains its queue and flushes its transports, then exit with `128 + signal`. A handler someone
    installed earlier runs after the drain instead of the exit, so this never takes over a signal
    the application already handles. Must be called from the main thread (a `signal` rule), where
    it raises `ValueError` otherwise."""
    if threading.current_thread() is not threading.main_thread():
        raise ValueError(
            "install_signal_handlers must be called from the main thread — Python only lets the "
            "main thread install signal handlers; call it at start-up, before creating threads"
        )
    for number in signals:
        previous = signal.getsignal(number)

        def handler(signum: int, frame: object, previous: object = previous) -> None:
            logger.close(timeout)
            if callable(previous) and previous is not signal.default_int_handler:
                previous(signum, frame)
            else:
                sys.exit(128 + signum)

        signal.signal(number, handler)


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

    dispatchers: dict[int, object] = {}
    transports: dict[int, Transport] = {}
    for logger in loggers:
        if logger._dispatcher is not None:
            dispatchers[id(logger._dispatcher)] = logger._dispatcher
        for transport in logger.transports:
            if transport not in already_closed:
                transports[id(transport)] = transport

    for dispatcher in dispatchers.values():
        try:
            # Drains the queue, stops the worker and closes the transports it holds.
            if not dispatcher.close(timeout):  # type: ignore[attr-defined]
                _logger.warning(
                    "logquill: %.1fs wasn't enough to flush every queued record at exit \u2014 "
                    "a transport is slow or unreachable; call logger.close(timeout=...) "
                    "yourself with a longer timeout if these records matter",
                    timeout,
                )
        except Exception:
            _logger.exception("logquill: failed to drain the async queue at exit")

    for transport in transports.values():
        mark_closed(transport)
