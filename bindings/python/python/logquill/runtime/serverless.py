from __future__ import annotations

import functools
import inspect
from typing import Any, Callable, Sequence, TypeVar, cast

from logquill.core.logger import Logger

F = TypeVar("F", bound=Callable[..., Any])

LoggerOrLoggers = Logger | Sequence[Logger]


def _as_loggers(loggers: LoggerOrLoggers) -> tuple[Logger, ...]:
    return (loggers,) if isinstance(loggers, Logger) else tuple(loggers)


def with_lambda(loggers: LoggerOrLoggers, *, timeout: float | None = 5.0) -> Callable[[F], F]:
    """Wrap a serverless function handler so any log records still queued
    on a non-blocking `Logger` (`async_dispatch=True`) are flushed before
    the handler's result (or exception) is returned to the platform.

    This exists because a serverless execution environment can freeze or
    be torn down immediately after the handler returns — a record still
    sitting in `AsyncWorker`'s queue at that instant may never actually
    reach its transport. Wrapping the handler makes "flush before return"
    automatic instead of something every handler has to remember to do
    itself.

    Calls `logger.flush(timeout)` (or `flush_async` for an `async def`
    handler), never `logger.close()` — a warm container reuses the same
    `Logger`/transports on its next invocation, and `close()` would
    release resources (e.g. an open file handle, a pooled HTTP connection)
    that next invocation needs. Flushing happens whether the handler
    returns normally or raises, so an unhandled error still ships its logs.

    Despite the name, this also covers GCP Cloud Functions and Azure
    Functions handlers — see `with_cloud_function`/`with_azure_function`,
    which are the same decorator under a name matching that platform. The
    flush-before-return behavior needed is identical across all three;
    only the handler's own argument signature (which this decorator never
    inspects) differs per platform.

    `loggers` accepts a single `Logger` or a sequence of them, for a
    handler that logs through more than one (e.g. an app logger and a
    separate audit logger).
    """
    targets = _as_loggers(loggers)

    def decorator(func: F) -> F:
        """Wraps `func`, choosing the async or sync flush path based on
        whether `func` itself is a coroutine function."""
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                """Awaits `func`, then `flush_async`es every target logger
                in a `finally` block so a raised exception still ships its
                logs."""
                try:
                    return await func(*args, **kwargs)
                finally:
                    for logger in targets:
                        await logger.flush_async(timeout)

            return cast(F, async_wrapper)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            """Calls `func`, then flushes every target logger in a `finally`
            block so a raised exception still ships its logs."""
            try:
                return func(*args, **kwargs)
            finally:
                for logger in targets:
                    logger.flush(timeout)

        return cast(F, wrapper)

    return decorator


#: Same decorator as `with_lambda`, named for a GCP Cloud Functions handler.
#: See `with_lambda`'s docstring — the flush-before-return behavior is
#: identical across platforms; only the name differs, to read naturally at
#: each platform's own handler definition.
with_cloud_function = with_lambda

#: Same decorator as `with_lambda`, named for an Azure Functions handler.
with_azure_function = with_lambda
