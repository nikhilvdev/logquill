"""`logquill.instrument.litellm(logger)` — patches `litellm.completion`/
`litellm.acompletion` so every call emits a `logger.llm_call(...)` with no
call-site changes. Since litellm itself fans out to 100+ providers behind one
interface, this one instrumentation covers all of them.

Requires the optional `litellm` package (`pip install
logquill[instrument-litellm]`), imported lazily inside `instrument()`.

**Scope**: only non-streaming calls are instrumented; see
`logquill.instrument.anthropic` for why streaming is a tracked gap rather
than a silent one.
"""

from __future__ import annotations

import functools
import time
from typing import Any

from logquill.core.logger import Logger
from logquill.instrument._util import Instrumenter, Patch, apply, record_safely


def _provider(response: Any) -> str | None:
    hidden = getattr(response, "_hidden_params", None)
    provider = hidden.get("custom_llm_provider") if isinstance(hidden, dict) else None
    return provider if isinstance(provider, str) else None


def _finish_reason(response: Any) -> str | None:
    choices = getattr(response, "choices", None)
    if not choices:
        return None
    return getattr(choices[0], "finish_reason", None)


def _record(logger: Logger, kwargs: dict[str, Any], response: Any, duration_ms: float) -> None:
    usage = getattr(response, "usage", None)
    logger.llm_call(
        model=getattr(response, "model", None) or kwargs.get("model"),
        tokens_in=getattr(usage, "prompt_tokens", None),
        tokens_out=getattr(usage, "completion_tokens", None),
        latency_ms=duration_ms,
        finish_reason=_finish_reason(response),
        provider=_provider(response),
    )


def _wrap_sync(original: Any, logger: Logger) -> Any:
    @functools.wraps(original)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("stream"):
            return original(*args, **kwargs)
        start = time.monotonic()
        response = original(*args, **kwargs)
        record_safely(lambda: _record(logger, kwargs, response, (time.monotonic() - start) * 1000))
        return response

    return wrapper


def _wrap_async(original: Any, logger: Logger) -> Any:
    @functools.wraps(original)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("stream"):
            return await original(*args, **kwargs)
        start = time.monotonic()
        response = await original(*args, **kwargs)
        record_safely(lambda: _record(logger, kwargs, response, (time.monotonic() - start) * 1000))
        return response

    return wrapper


def _apply(logger: Logger) -> list[Patch]:
    try:
        import litellm as litellm_module
    except ImportError as exc:
        raise ImportError(
            "logquill.instrument.litellm requires the optional `litellm` package — "
            "install with `pip install logquill[instrument-litellm]`."
        ) from exc
    return [
        apply(litellm_module, "completion", lambda original: _wrap_sync(original, logger)),
        apply(litellm_module, "acompletion", lambda original: _wrap_async(original, logger)),
    ]


litellm = Instrumenter("litellm", _apply)
"""Call `litellm(logger)` to instrument, `litellm.uninstrument()` to undo it.
See the module docstring for what's covered."""
