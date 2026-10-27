from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from logquill.core.levels import Level
from logquill.core.records import LogRecord, build_llm_block

if TYPE_CHECKING:
    from logquill.core.logger import Logger


def resolve_lazy(meta: dict[str, Any]) -> dict[str, Any]:
    """Replace every callable value in `meta` with what it returns. A callable
    that raises must not crash the caller that's just trying to log, so it
    becomes a placeholder naming the error instead."""
    resolved: dict[str, Any] = {}
    for key, value in meta.items():
        if callable(value):
            try:
                value = value()
            except Exception as exc:
                value = f"<lazy value raised {type(exc).__name__}: {exc}>"
        resolved[key] = value
    return resolved


def caller_info(depth: int) -> dict[str, Any] | None:
    """Where the code that logged is, `depth` frames further up the stack.

    Frame 0 is this function, 1 is `Logger._log`, 2 is the `OptLogger` method
    that called it, and 3 is the code that called that — `depth=0` — so every
    `OptLogger` method must call `Logger._log` directly for this count to hold.
    Returns `None` if the stack isn't `depth` frames deep.
    """
    try:
        frame = sys._getframe(3 + depth)
    except ValueError:
        return None
    return {
        "module": frame.f_globals.get("__name__"),
        "function": frame.f_code.co_name,
        "line": frame.f_lineno,
        "file": frame.f_code.co_filename,
    }


class OptLogger:
    """A view of a `Logger` with per-call options applied, from
    `Logger.opt(...)`. It has the same logging methods as `Logger`, and writes
    through the logger it came from — same level, plugins and transports.

    - `lazy=True`: callable `meta` values are called only if the record is
      actually going to be emitted, so an expensive value costs nothing when
      the logger's level filters the call out:

          logger.opt(lazy=True).debug("state", dump=lambda: expensive_dump())

      The callables run on the calling thread, before the record is queued for
      any async dispatch, so they see the state at the moment of the call.
    - `depth=N`: adds `meta.caller` (`module`, `function`, `line`, `file`)
      naming the code that logged, `N` frames up from the direct caller. Use
      it inside a wrapper or decorator so the record points at the wrapper's
      caller instead of the wrapper:

          def audit(message):
              logger.opt(depth=1).info(message)  # reports audit()'s caller
    """

    def __init__(self, logger: Logger, *, lazy: bool = False, depth: int | None = None) -> None:
        """See the class docstring. `depth` must be `None` (don't record a
        caller) or a non-negative integer."""
        if depth is not None and depth < 0:
            raise ValueError(f"opt(depth=...) must be >= 0, got {depth}")
        self._logger = logger
        self._lazy = lazy
        self._depth = depth

    def trace(self, message: str, /, **meta: Any) -> LogRecord | None:
        """`Logger.trace` with this view's options."""
        return self._logger._log(Level.TRACE, message, meta, lazy=self._lazy, depth=self._depth)

    def debug(self, message: str, /, **meta: Any) -> LogRecord | None:
        """`Logger.debug` with this view's options."""
        return self._logger._log(Level.DEBUG, message, meta, lazy=self._lazy, depth=self._depth)

    def info(self, message: str, /, **meta: Any) -> LogRecord | None:
        """`Logger.info` with this view's options."""
        return self._logger._log(Level.INFO, message, meta, lazy=self._lazy, depth=self._depth)

    def warn(self, message: str, /, **meta: Any) -> LogRecord | None:
        """`Logger.warn` with this view's options."""
        return self._logger._log(Level.WARN, message, meta, lazy=self._lazy, depth=self._depth)

    def error(self, message: str, /, **meta: Any) -> LogRecord | None:
        """`Logger.error` with this view's options."""
        return self._logger._log(Level.ERROR, message, meta, lazy=self._lazy, depth=self._depth)

    def fatal(self, message: str, /, **meta: Any) -> LogRecord | None:
        """`Logger.fatal` with this view's options."""
        return self._logger._log(Level.FATAL, message, meta, lazy=self._lazy, depth=self._depth)

    def thought(self, message: str, /, **meta: Any) -> LogRecord | None:
        """`Logger.thought` with this view's options."""
        return self._logger._log(
            Level.INFO, message, {"kind": "thought", **meta}, lazy=self._lazy, depth=self._depth
        )

    def action(self, message: str, /, **meta: Any) -> LogRecord | None:
        """`Logger.action` with this view's options."""
        return self._logger._log(
            Level.INFO, message, {"kind": "action", **meta}, lazy=self._lazy, depth=self._depth
        )

    def observation(self, message: str, /, **meta: Any) -> LogRecord | None:
        """`Logger.observation` with this view's options."""
        return self._logger._log(
            Level.INFO, message, {"kind": "observation", **meta}, lazy=self._lazy, depth=self._depth
        )

    def decision(self, message: str, /, **meta: Any) -> LogRecord | None:
        """`Logger.decision` with this view's options."""
        return self._logger._log(
            Level.INFO, message, {"kind": "decision", **meta}, lazy=self._lazy, depth=self._depth
        )

    def llm_call(
        self,
        message: str = "llm_call",
        /,
        *,
        model: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: float | None = None,
        latency_ms: float | None = None,
        finish_reason: str | None = None,
        **meta: Any,
    ) -> LogRecord | None:
        """`Logger.llm_call` with this view's options."""
        llm = build_llm_block(
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
        )
        return self._logger._log(
            Level.INFO,
            message,
            {"kind": "action", **meta},
            lazy=self._lazy,
            depth=self._depth,
            llm=llm,
        )
