"""`logquill.instrument.openai(logger)` — patches the OpenAI Python SDK so
every `client.chat.completions.create(...)` and `client.responses.create(...)`
call (sync and async) emits a `logger.llm_call(...)` with no call-site changes.

Requires the optional `openai` package (`pip install
logquill[instrument-openai]`), imported lazily inside `instrument()`.

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


def _chat_finish_reason(response: Any) -> str | None:
    choices = getattr(response, "choices", None)
    if not choices:
        return None
    return getattr(choices[0], "finish_reason", None)


def _record_chat(logger: Logger, kwargs: dict[str, Any], response: Any, duration_ms: float) -> None:
    usage = getattr(response, "usage", None)
    logger.llm_call(
        model=getattr(response, "model", None) or kwargs.get("model"),
        tokens_in=getattr(usage, "prompt_tokens", None),
        tokens_out=getattr(usage, "completion_tokens", None),
        latency_ms=duration_ms,
        finish_reason=_chat_finish_reason(response),
        provider="openai",
    )


def _responses_finish_reason(response: Any) -> str | None:
    incomplete = getattr(response, "incomplete_details", None)
    reason = getattr(incomplete, "reason", None) if incomplete is not None else None
    return reason if isinstance(reason, str) else getattr(response, "status", None)


def _record_responses(
    logger: Logger, kwargs: dict[str, Any], response: Any, duration_ms: float
) -> None:
    usage = getattr(response, "usage", None)
    logger.llm_call(
        model=getattr(response, "model", None) or kwargs.get("model"),
        tokens_in=getattr(usage, "input_tokens", None),
        tokens_out=getattr(usage, "output_tokens", None),
        latency_ms=duration_ms,
        finish_reason=_responses_finish_reason(response),
        provider="openai",
    )


def _wrap_sync(original: Any, logger: Logger, record: Any) -> Any:
    @functools.wraps(original)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("stream"):
            return original(self, *args, **kwargs)
        start = time.monotonic()
        response = original(self, *args, **kwargs)
        record_safely(lambda: record(logger, kwargs, response, (time.monotonic() - start) * 1000))
        return response

    return wrapper


def _wrap_async(original: Any, logger: Logger, record: Any) -> Any:
    @functools.wraps(original)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("stream"):
            return await original(self, *args, **kwargs)
        start = time.monotonic()
        response = await original(self, *args, **kwargs)
        record_safely(lambda: record(logger, kwargs, response, (time.monotonic() - start) * 1000))
        return response

    return wrapper


def _apply(logger: Logger) -> list[Patch]:
    try:
        from openai.resources import responses as responses_module
        from openai.resources.chat import completions as completions_module
    except ImportError as exc:
        raise ImportError(
            "logquill.instrument.openai requires the optional `openai` package — "
            "install with `pip install logquill[instrument-openai]`."
        ) from exc
    return [
        apply(
            completions_module.Completions,
            "create",
            lambda original: _wrap_sync(original, logger, _record_chat),
        ),
        apply(
            completions_module.AsyncCompletions,
            "create",
            lambda original: _wrap_async(original, logger, _record_chat),
        ),
        apply(
            responses_module.Responses,
            "create",
            lambda original: _wrap_sync(original, logger, _record_responses),
        ),
        apply(
            responses_module.AsyncResponses,
            "create",
            lambda original: _wrap_async(original, logger, _record_responses),
        ),
    ]


openai = Instrumenter("openai", _apply)
"""Call `openai(logger)` to instrument, `openai.uninstrument()` to undo it.
Covers both the Chat Completions and Responses APIs. See the module
docstring for what's covered."""
