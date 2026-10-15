from __future__ import annotations

from datetime import datetime
from typing import Any

try:
    from crewai.events import (  # type: ignore[import-not-found]
        AgentExecutionCompletedEvent,
        AgentExecutionErrorEvent,
        AgentExecutionStartedEvent,
        BaseEventListener,
        CrewKickoffCompletedEvent,
        CrewKickoffFailedEvent,
        CrewKickoffStartedEvent,
        LLMCallCompletedEvent,
        LLMCallFailedEvent,
        LLMCallStartedEvent,
        TaskCompletedEvent,
        TaskFailedEvent,
        TaskStartedEvent,
        ToolUsageErrorEvent,
        ToolUsageFinishedEvent,
        ToolUsageStartedEvent,
    )
except ImportError as exc:
    raise ImportError(
        "logquill.adapters.crewai requires the optional `crewai` dependency — "
        "install with `pip install logquill[crewai]`."
    ) from exc

from logquill.adapters.base import LogQuillAdapter
from logquill.core.logger import Logger
from logquill.tracing.span import SpanContext


def _span_ids(event: Any) -> dict[str, Any]:
    ids: dict[str, Any] = {"span_id": event.event_id}
    if event.parent_event_id is not None:
        ids["parent_span_id"] = event.parent_event_id
    return ids


def _closing_ids(event: Any) -> dict[str, Any]:
    ids: dict[str, Any] = {"span_id": event.started_event_id or event.event_id}
    if event.parent_event_id is not None:
        ids["parent_span_id"] = event.parent_event_id
    return ids


# `type: ignore[misc]` — same reason as `LangChainAdapter`: `BaseEventListener`
# types as `Any` whenever `crewai` isn't installed in the environment running
# mypy (it's optional, never in this project's `dev` extra — see
# pyproject.toml), and mypy refuses to let a class subclass something typed
# `Any`. With the real package installed, this subclasses the genuine
# `BaseEventListener` and the ignore is inert.
class CrewAIAdapter(LogQuillAdapter, BaseEventListener):  # type: ignore[misc]
    """Maps CrewAI's event-bus events onto LogQuill calls.

    Unlike `LangChainAdapter`, correlation doesn't rely on any ambient state
    this library tracks — CrewAI's own event bus already threads
    `event.parent_event_id` (this event's logical parent) and, on every
    "ended" event, `event.started_event_id` (the matching "started" event's
    id) through a `contextvars`-backed scope stack internal to CrewAI. Those
    are used directly as `span_id`/`parent_span_id`, the same way
    `LangChainAdapter` uses LangChain's `run_id`/`parent_run_id` — field
    renaming, not translation.

    A crew kickoff and each task within it open/close a `span()`; agent
    execution, tool usage, and LLM calls are `.action()`/`.observation()`/
    `.error()` pairs carrying `duration_ms` — `ToolUsageFinishedEvent`
    already carries its own `started_at`/`finished_at`, used directly;
    agent execution and LLM calls fall back to timing this adapter records
    itself at the matching start event.

    Instantiating this class registers its handlers immediately (that's
    `BaseEventListener`'s own behavior) — keep a reference alive for as long
    as you want it active, the same way any CrewAI custom listener works:

        from logquill import Logger, RunPlugin
        from logquill.adapters.crewai import CrewAIAdapter

        log = Logger("app")
        listener = CrewAIAdapter(log.child("agent").use(RunPlugin()))
        crew = Crew(agents=[...], tasks=[...])  # listener is now active
        crew.kickoff()

    If a listener is attached mid-run (so this adapter never saw the
    matching "started" event for something already in progress), the
    corresponding "ended" event is dropped rather than guessed at — the same
    posture `SamplingPlugin`/`AlertingPlugin` take toward not fabricating
    data they don't actually have.
    """

    def __init__(self, agent_log: Logger) -> None:
        """Registers this listener's handlers on CrewAI's event bus
        immediately (`BaseEventListener.__init__`'s own behavior) — keep a
        reference alive for as long as you want it active."""
        LogQuillAdapter.__init__(self, agent_log)
        self._open_spans: dict[str, SpanContext] = {}
        self._call_starts: dict[str, datetime] = {}
        BaseEventListener.__init__(self)  # registers handlers; needs self.log set first

    def _open_span(self, name: str, event: Any) -> None:
        span = self.log.span(name, span_id=event.event_id, parent_span_id=event.parent_event_id)
        span.__enter__()
        self._open_spans[event.event_id] = span

    def _close_span(self, event: Any, error: BaseException | None = None) -> None:
        span = self._open_spans.pop(event.started_event_id or "", None)
        if span is None:
            return
        if error is not None:
            span.__exit__(type(error), error, None)
        else:
            span.__exit__(None, None, None)

    def _step_start(self, name: str, event: Any) -> None:
        self._call_starts[event.event_id] = event.timestamp
        self.log.action(name, **_span_ids(event))

    def _step_end(self, name: str, event: Any, *, error: str | None = None) -> None:
        start = self._call_starts.pop(event.started_event_id or "", None)
        ids = _closing_ids(event)
        if start is not None:
            ids["duration_ms"] = round((event.timestamp - start).total_seconds() * 1000, 3)
        if error is not None:
            self.log.error(name, error=error, **ids)
        else:
            self.log.observation(name, **ids)

    # Registered via plain calls rather than `@crewai_event_bus.on(...)`
    # decorator syntax below — `crewai_event_bus` types as `Any` whenever
    # `crewai` isn't installed (see the `type: ignore[misc]` note on the
    # class itself), and mypy strict's `disallow_untyped_decorators` flags
    # `@`-applying an `Any`-typed decorator even though the wrapped method
    # itself is fully annotated. A plain call sidesteps that check.
    def setup_listeners(self, crewai_event_bus: Any) -> None:
        """`BaseEventListener`'s required override: subscribes every CrewAI
        event this adapter translates (crew/task/agent/tool/LLM
        start/end/error) to its matching handler on `crewai_event_bus`."""
        crewai_event_bus.on(CrewKickoffStartedEvent)(self._on_crew_started)
        crewai_event_bus.on(CrewKickoffCompletedEvent)(self._on_crew_completed)
        crewai_event_bus.on(CrewKickoffFailedEvent)(self._on_crew_failed)
        crewai_event_bus.on(TaskStartedEvent)(self._on_task_started)
        crewai_event_bus.on(TaskCompletedEvent)(self._on_task_completed)
        crewai_event_bus.on(TaskFailedEvent)(self._on_task_failed)
        crewai_event_bus.on(AgentExecutionStartedEvent)(self._on_agent_started)
        crewai_event_bus.on(AgentExecutionCompletedEvent)(self._on_agent_completed)
        crewai_event_bus.on(AgentExecutionErrorEvent)(self._on_agent_error)
        crewai_event_bus.on(ToolUsageStartedEvent)(self._on_tool_started)
        crewai_event_bus.on(ToolUsageFinishedEvent)(self._on_tool_finished)
        crewai_event_bus.on(ToolUsageErrorEvent)(self._on_tool_error)
        crewai_event_bus.on(LLMCallStartedEvent)(self._on_llm_started)
        crewai_event_bus.on(LLMCallCompletedEvent)(self._on_llm_completed)
        crewai_event_bus.on(LLMCallFailedEvent)(self._on_llm_failed)

    def _on_crew_started(self, source: Any, event: Any) -> None:
        self._open_span(f"crew:{event.crew_name or 'crew'}", event)

    def _on_crew_completed(self, source: Any, event: Any) -> None:
        self._close_span(event)

    def _on_crew_failed(self, source: Any, event: Any) -> None:
        self._close_span(event, error=RuntimeError(event.error))

    def _on_task_started(self, source: Any, event: Any) -> None:
        self._open_span(f"task:{event.task_name or event.task_id or 'task'}", event)

    def _on_task_completed(self, source: Any, event: Any) -> None:
        self._close_span(event)

    def _on_task_failed(self, source: Any, event: Any) -> None:
        error_cls = event.error_type or RuntimeError
        self._close_span(event, error=error_cls(event.error))

    def _on_agent_started(self, source: Any, event: Any) -> None:
        self._step_start(f"agent:{event.agent.role}", event)

    def _on_agent_completed(self, source: Any, event: Any) -> None:
        self._step_end(f"agent:{event.agent.role}", event)

    def _on_agent_error(self, source: Any, event: Any) -> None:
        self._step_end(f"agent:{event.agent.role}", event, error=event.error)

    def _on_tool_started(self, source: Any, event: Any) -> None:
        self.log.action(event.tool_name, **_span_ids(event))

    def _on_tool_finished(self, source: Any, event: Any) -> None:
        duration_ms = (event.finished_at - event.started_at).total_seconds() * 1000
        self.log.observation(
            event.tool_name, duration_ms=round(duration_ms, 3), **_closing_ids(event)
        )

    def _on_tool_error(self, source: Any, event: Any) -> None:
        self.log.error(event.tool_name, error=str(event.error), **_closing_ids(event))

    def _on_llm_started(self, source: Any, event: Any) -> None:
        self._step_start("llm_call", event)

    def _on_llm_completed(self, source: Any, event: Any) -> None:
        self._step_end("llm_call", event)

    def _on_llm_failed(self, source: Any, event: Any) -> None:
        self._step_end("llm_call", event, error=event.error)
