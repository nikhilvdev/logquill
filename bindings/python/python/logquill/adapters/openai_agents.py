"""`OpenAIAgentsAdapter` — translates the OpenAI Agents SDK's `RunHooks`
lifecycle callbacks into `.thought()/.action()/.observation()/.decision()`
and `span()` calls, per `LogQuillAdapter`'s "thin mapping, not a
reimplementation" contract.

Requires the optional `openai-agents` package (`pip install
logquill[openai-agents]`), imported lazily — importing this module before
it's installed raises an actionable `ImportError`, and importing `logquill`
itself never imports `agents`.
"""

from __future__ import annotations

import time
from typing import Any

try:
    from agents.lifecycle import RunHooks
except ImportError as exc:
    raise ImportError(
        "logquill.adapters.openai_agents requires the optional `openai-agents` "
        "package — install with `pip install logquill[openai-agents]`."
    ) from exc

from logquill.adapters.base import LogQuillAdapter
from logquill.core.logger import Logger
from logquill.tracing.span import SpanContext, new_span_id


def _agent_name(agent: Any) -> str:
    name = getattr(agent, "name", None)
    return name if isinstance(name, str) and name else "agent"


def _tool_name(tool: Any) -> str:
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) and name else "tool"


def _usage_fields(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    return {
        "tokens_in": getattr(usage, "input_tokens", None),
        "tokens_out": getattr(usage, "output_tokens", None),
    }


# `type: ignore[misc]` — `RunHooks` types as `Any` whenever `openai-agents`
# isn't installed in the environment running mypy (an optional dependency,
# never in this project's `dev` extra — see pyproject.toml), and mypy refuses
# to let a class subclass something typed `Any`. With the real package
# installed, this subclasses the genuine `RunHooks` and the ignore is inert.
class OpenAIAgentsAdapter(LogQuillAdapter, RunHooks):  # type: ignore[misc]
    """Pass an instance as `Runner.run(..., hooks=...)` — no separate
    registration step needed.

    | OpenAI Agents SDK hook            | LogQuill call                     |
    |------------------------------------|------------------------------------|
    | `on_agent_start` / `on_agent_end`  | opens/closes a `span()` |
    | `on_llm_end`                       | `.llm_call()` with token usage and elapsed time |
    | `on_tool_start` / `on_tool_end`    | `.action(tool=...)` / `.observation(tool=...)` |
    | `on_handoff`                       | `.decision()` naming `from_agent`/`to_agent` |

    Every agent activation gets its own `invoke_agent {agent.name}` span, not
    just the outermost — a handoff's target is its own nested span, never a
    reuse of its predecessor's. `on_llm_end`'s token counts and `duration_ms`
    (timed since the matching `on_llm_start`) come straight from
    `ModelResponse.usage`. Tagging `.action()`/`.observation()` with `tool=`
    is what makes them show up as `execute_tool` spans under `OTLPTransport`
    and get `retry_count` tracked automatically.

    A run's `RunContextWrapper`/`AgentHookContext` has no id of its own to
    key spans by (unlike LangChain's `run_id`), so this adapter uses the
    context object's identity — one `Runner.run()` call reuses one context
    throughout, so nesting (including across a handoff) still comes out
    right. The one sharp edge: if the *same* tool is invoked twice
    concurrently in one run, their `on_tool_start`/`on_tool_end` pairs can
    cross-attribute duration, since the SDK's hooks don't hand this adapter
    a per-call id to key on — sequential calls (the common case, and the
    SDK's default) are unaffected.
    """

    def __init__(self, agent_log: Logger) -> None:
        LogQuillAdapter.__init__(self, agent_log)
        self._agent_spans: dict[int, list[SpanContext]] = {}
        self._llm_starts: dict[int, float] = {}
        self._tool_spans: dict[tuple[int, str], list[float]] = {}

    def _stack(self, context: Any) -> list[SpanContext]:
        return self._agent_spans.setdefault(id(context), [])

    async def on_agent_start(self, context: Any, agent: Any) -> None:
        """Opens a span for this agent's activation, nested under whichever
        agent (if any) is already active in this run — including a handoff's
        target, which gets its own nested span rather than reusing its
        predecessor's."""
        stack = self._stack(context)
        parent_span_id = stack[-1]._span_id if stack else None
        span = self.log.span(
            _agent_name(agent),
            span_id=new_span_id(),
            parent_span_id=parent_span_id,
            operation="invoke_agent",
            agent_name=_agent_name(agent),
        )
        span.__enter__()
        stack.append(span)

    async def on_agent_end(self, context: Any, agent: Any, output: Any) -> None:
        """Closes the span opened by the matching `on_agent_start`."""
        stack = self._stack(context)
        if stack:
            stack.pop().__exit__(None, None, None)
        if not stack:
            self._agent_spans.pop(id(context), None)

    async def on_llm_start(
        self, context: Any, agent: Any, system_prompt: str | None, input_items: Any
    ) -> None:
        """Records the call's start time, for the matching `on_llm_end`'s
        `duration_ms`."""
        self._llm_starts[id(context)] = time.monotonic()

    async def on_llm_end(self, context: Any, agent: Any, response: Any) -> None:
        """Emits `.llm_call()` with token usage and elapsed time."""
        start = self._llm_starts.pop(id(context), None)
        duration_ms = round((time.monotonic() - start) * 1000, 3) if start is not None else None
        model = getattr(agent, "model", None)
        self.log.llm_call(
            model=model if isinstance(model, str) else None,
            latency_ms=duration_ms,
            **_usage_fields(response),
        )

    async def on_tool_start(self, context: Any, agent: Any, tool: Any) -> None:
        """Emits `.action(tool=...)` and records the call's start time."""
        name = _tool_name(tool)
        self._tool_spans.setdefault((id(context), name), []).append(time.monotonic())
        self.log.action(f"call {name}", tool=name, agent_name=_agent_name(agent))

    async def on_tool_end(self, context: Any, agent: Any, tool: Any, result: Any) -> None:
        """Emits `.observation(tool=...)` with `duration_ms` measured since
        the matching `on_tool_start` — see the class docstring for the one
        case (concurrent calls of the same tool) this timing can misattribute."""
        name = _tool_name(tool)
        starts = self._tool_spans.get((id(context), name))
        start = starts.pop() if starts else None
        duration_ms = round((time.monotonic() - start) * 1000, 3) if start is not None else None
        self.log.observation(f"{name} done", tool=name, duration_ms=duration_ms)

    async def on_handoff(self, context: Any, from_agent: Any, to_agent: Any) -> None:
        """Emits `.decision()` naming the handoff's source and destination
        agents."""
        self.log.decision(
            "handoff", from_agent=_agent_name(from_agent), to_agent=_agent_name(to_agent)
        )
