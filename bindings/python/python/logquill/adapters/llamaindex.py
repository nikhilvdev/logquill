from __future__ import annotations

from typing import Any

try:
    from llama_index.core.instrumentation import get_dispatcher  # type: ignore[import-not-found]
    from llama_index.core.instrumentation.event_handlers import (  # type: ignore[import-not-found]
        BaseEventHandler,
    )
    from llama_index.core.instrumentation.span_handlers import (  # type: ignore[import-not-found]
        SimpleSpanHandler,
    )
    from pydantic import ConfigDict  # type: ignore[import-not-found]
except ImportError as exc:
    raise ImportError(
        "logquill.adapters.llamaindex requires the optional `llama-index-core` "
        "dependency — install with `pip install logquill[llamaindex]`."
    ) from exc

from logquill.adapters.base import LogQuillAdapter
from logquill.core.logger import Logger

# Event classes carrying no useful correlation id of their own beyond the
# span they fired in — every `XxxStartEvent`/`XxxEndEvent`/`XxxErrorEvent`
# pair shares its enclosing span's `span_id` rather than a call-specific id
# (confirmed against `llama_index/core/instrumentation/events/*.py` and
# `llama_index_instrumentation/base/event.py` upstream — `BaseEvent` has no
# "started_event_id"-equivalent, unlike CrewAI's events). So instead of
# enumerating every concrete event class (~25 across llm/retrieval/query/
# synthesis/agent/embedding/chat_engine, and growing), events are classified
# generically by their `class_name()` suffix — `*StartEvent` -> `.action()`,
# `*EndEvent` -> `.observation()`, `*ErrorEvent` -> `.error()` — which also
# means a new event type LlamaIndex adds later needs no adapter change to
# show up correctly.
_SKIPPED_SUFFIXES = ("InProgressEvent", "DeltaReceivedEvent")


# `type: ignore[misc]` on both handler classes below — same reason as
# `LangChainAdapter`/`CrewAIAdapter`: their real bases type as `Any` whenever
# `llama-index-core` isn't installed in the environment running mypy (it's
# optional, never in this project's `dev` extra — see pyproject.toml), and
# mypy refuses to let a class subclass something typed `Any`. With the real
# package installed, these subclass the genuine base classes and the ignore
# is inert.
class _SpanLogger(SimpleSpanHandler):  # type: ignore[misc]
    """Logs LlamaIndex's own method-level spans (`query()`, `chat()`,
    `retrieve()`, ...) — each one decorated internally with `@dispatcher.span`
    — as they open and close. `SimpleSpanHandler` already tracks id/parent
    id/duration for every span; this only adds logging on top of it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    log: Logger

    def prepare_to_exit_span(
        self,
        id_: str,
        bound_args: Any,
        instance: Any = None,
        result: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Logs a successfully-completed span (`.info()`, with `duration_ms`
        and `parent_span_id` if nested) after delegating to
        `SimpleSpanHandler` for the actual span bookkeeping."""
        span = super().prepare_to_exit_span(id_, bound_args, instance, result, **kwargs)
        self._log_close(id_, span)
        return span

    def prepare_to_drop_span(
        self,
        id_: str,
        bound_args: Any,
        instance: Any = None,
        err: BaseException | None = None,
        **kwargs: Any,
    ) -> Any:
        """Logs a span that exited via exception (`.error()`, with `err`'s
        message) after delegating to `SimpleSpanHandler` for the actual span
        bookkeeping."""
        span = super().prepare_to_drop_span(id_, bound_args, instance, err, **kwargs)
        self._log_close(id_, span, error=err)
        return span

    def _log_close(self, id_: str, span: Any, error: BaseException | None = None) -> None:
        # LlamaIndex names a span `f"{qualified_method_name}-{uuid}"` — the
        # message drops the uuid suffix (`.partition` is a no-op, not an
        # error, if `id_` happens to have none); the full `id_` stays the
        # `span_id` so it's still unique for correlation.
        name = id_.partition("-")[0]
        meta: dict[str, Any] = {"span_id": id_, "kind": "span"}
        if span is not None:
            meta["duration_ms"] = round(span.duration * 1000, 3)
            if span.parent_id is not None:
                meta["parent_span_id"] = span.parent_id
        if error is not None:
            self.log.error(name, error=f"{type(error).__name__}: {error}", **meta)
        else:
            self.log.info(name, **meta)


class _EventLogger(BaseEventHandler):  # type: ignore[misc]
    """Logs LlamaIndex's named events (LLM calls, retrieval, synthesis,
    embedding, agent steps, ...), nested under whichever span was open when
    each one fired via `event.span_id` -> `meta.parent_span_id`."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    log: Logger

    def handle(self, event: Any, **kwargs: Any) -> Any:
        """`BaseEventHandler`'s required override: classifies `event` by its
        `class_name()` suffix (`*StartEvent`/`*EndEvent`/`*ErrorEvent`) and
        forwards it as the matching `.action()`/`.observation()`/`.error()`
        call; a few noisy progress/delta event types are skipped entirely."""
        name = event.class_name()
        if name.endswith(_SKIPPED_SUFFIXES):
            return None

        meta: dict[str, Any] = {}
        span_id = getattr(event, "span_id", None)
        if span_id is not None:
            meta["parent_span_id"] = span_id

        if name.endswith("ErrorEvent"):
            error = getattr(event, "exception", None) or getattr(event, "error", None)
            self.log.error(name, error=str(error) if error is not None else "", **meta)
        elif name.endswith("StartEvent"):
            self.log.action(name, **meta)
        elif name.endswith("EndEvent"):
            self.log.observation(name, **meta)
        else:
            self.log.action(name, **meta)
        return None


class LlamaIndexAdapter(LogQuillAdapter):
    """Maps LlamaIndex's instrumentation module onto LogQuill calls.

    Unlike `LangChainAdapter`/`CrewAIAdapter`, LlamaIndex splits
    instrumentation into two cooperating registrations on a shared global
    dispatcher — a span handler (LlamaIndex's own internal method calls,
    each already wrapped in a span by the framework) and an event handler
    (named events fired *within* those spans) — so this adapter holds one
    of each internally rather than being a handler itself:

        from logquill import Logger, RunPlugin
        from logquill.adapters.llamaindex import LlamaIndexAdapter

        log = Logger("app")
        adapter = LlamaIndexAdapter(log.child("agent").use(RunPlugin()))  # active immediately
        index.as_query_engine().query("...")

    A `query()`/`chat()`/`retrieve()` call becomes a `span_id`/`duration_ms`
    record on completion (`meta.parent_span_id` set for a nested call, e.g.
    `retrieve()` inside `query()`); LLM calls, retrieval, synthesis,
    embedding, and agent-step events become `.action()`/`.observation()`/
    `.error()` records nested under whichever span was open via
    `meta.parent_span_id`. `llama-index-core` is never imported unless you
    import `logquill.adapters.llamaindex` yourself.
    """

    def __init__(self, agent_log: Logger) -> None:
        """Registers a span handler and an event handler on LlamaIndex's
        global instrumentation dispatcher immediately — active as soon as
        it's constructed, no separate "start" call needed."""
        super().__init__(agent_log)
        self._span_handler = _SpanLogger(log=agent_log)
        self._event_handler = _EventLogger(log=agent_log)
        dispatcher = get_dispatcher()
        dispatcher.add_span_handler(self._span_handler)
        dispatcher.add_event_handler(self._event_handler)
