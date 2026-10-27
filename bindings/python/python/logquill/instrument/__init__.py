"""One-line instrumentation for provider SDKs: patch a client library so every
LLM call it makes emits `logger.llm_call(...)` — token counts, model, finish
reason, latency — with no call-site changes anywhere in your code.

    import logquill.instrument as instrument

    instrument.anthropic(agent_log)
    # every client.messages.create(...) anywhere in the process now logs itself
    ...
    instrument.anthropic.uninstrument()

Each provider lives behind its own optional extra (`pip install
logquill[instrument-anthropic]`, `[instrument-openai]`, `[instrument-litellm]`)
and is imported lazily, inside the call to `instrument.<provider>(logger)` —
**importing `logquill`, or `logquill.instrument`, never imports a provider
SDK.** Calling an instrumenter twice without a matching `.uninstrument()`
raises, so a call is never wrapped twice.

Every instrumenter currently covers non-streaming calls only; a streaming
call is passed through untouched rather than partially instrumented — see
each provider module's docstring.
"""

from __future__ import annotations

from logquill.instrument.anthropic import anthropic
from logquill.instrument.litellm import litellm
from logquill.instrument.openai import openai

__all__ = ["anthropic", "litellm", "openai"]
