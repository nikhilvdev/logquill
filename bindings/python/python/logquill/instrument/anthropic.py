"""`logquill.instrument.anthropic(logger)` — patches the Anthropic Python
SDK so every `client.messages.create(...)` call (sync and async) emits a
`logger.llm_call(...)` with no call-site changes.

Requires the optional `anthropic` package (`pip install
logquill[instrument-anthropic]`), imported lazily inside `instrument()` —
importing `logquill.instrument` never imports it.

**Scope**: only non-streaming calls (`stream` unset or `False`) are
instrumented. A streaming call (`stream=True`, or `.stream()`) is passed
through untouched — accumulating usage across a stream safely, without
disturbing the SDK's own streaming ergonomics, needs more design than this
pass covers; it's a tracked gap, not a silent one (see the module docstring
in `logquill/instrument/__init__.py`).
"""

from __future__ import annotations

import functools
import inspect
import time
from typing import Any

from logquill.core.logger import Logger
from logquill.instrument._util import Instrumenter, Patch, apply, apply_to_instance, record_safely


def _record(logger: Logger, kwargs: dict[str, Any], response: Any, duration_ms: float) -> None:
    usage = getattr(response, "usage", None)
    logger.llm_call(
        model=getattr(response, "model", None) or kwargs.get("model"),
        tokens_in=getattr(usage, "input_tokens", None),
        tokens_out=getattr(usage, "output_tokens", None),
        latency_ms=duration_ms,
        finish_reason=getattr(response, "stop_reason", None),
        provider="anthropic",
    )


def _wrap_sync(original: Any, logger: Logger) -> Any:
    @functools.wraps(original)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("stream"):
            return original(self, *args, **kwargs)
        start = time.monotonic()
        response = original(self, *args, **kwargs)
        record_safely(lambda: _record(logger, kwargs, response, (time.monotonic() - start) * 1000))
        return response

    return wrapper


def _wrap_async(original: Any, logger: Logger) -> Any:
    @functools.wraps(original)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("stream"):
            return await original(self, *args, **kwargs)
        start = time.monotonic()
        response = await original(self, *args, **kwargs)
        record_safely(lambda: _record(logger, kwargs, response, (time.monotonic() - start) * 1000))
        return response

    return wrapper


def _apply(logger: Logger, client: Any = None) -> list[Patch]:
    if client is not None:  # one client only; the SDK need not be importable by us
        return [
            apply_to_instance(
                client.messages,
                "create",
                lambda original: (
                    _wrap_async if inspect.iscoroutinefunction(original) else _wrap_sync
                )(original, logger),
            )
        ]
    try:
        from anthropic.resources.messages import messages as messages_module
    except ImportError as exc:
        raise ImportError(
            "logquill.instrument.anthropic requires the optional `anthropic` package — "
            "install with `pip install logquill[instrument-anthropic]`."
        ) from exc
    return [
        apply(messages_module.Messages, "create", lambda original: _wrap_sync(original, logger)),
        apply(
            messages_module.AsyncMessages, "create", lambda original: _wrap_async(original, logger)
        ),
    ]


anthropic = Instrumenter("anthropic", _apply)
"""Call `anthropic(logger)` to instrument, `anthropic.uninstrument()` to
undo it. `anthropic(logger, client=client)` instruments just that client (sync or async); call
it once per client. See the module docstring for what's covered."""
