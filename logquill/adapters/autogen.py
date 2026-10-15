from __future__ import annotations

import logging
from typing import Any

try:
    from autogen_core import EVENT_LOGGER_NAME  # type: ignore[import-not-found]
except ImportError as exc:
    raise ImportError(
        "logquill.adapters.autogen requires the optional `autogen-core` "
        "dependency — install with `pip install logquill[autogen]`."
    ) from exc

from logquill.adapters.base import LogQuillAdapter
from logquill.core.logger import Logger

_ERROR_EVENT_NAMES = {
    "MessageHandlerException": "message_handler_exception",
    "AgentConstructionException": "agent_construction_exception",
}


class AutoGenAdapter(LogQuillAdapter, logging.Handler):
    """Maps (Microsoft) AutoGen's structured event logging onto LogQuill calls.

    Deliberately not `LogQuillAdapter`-plus-a-callback-registry like
    `LangChainAdapter`/`CrewAIAdapter`/`LlamaIndexAdapter` — AutoGen's
    actual integration point for this is a stdlib `logging.Handler`
    attached to `autogen_core.EVENT_LOGGER_NAME`: model clients and tools
    log structured event *objects* (not strings) there via
    `logging.getLogger(EVENT_LOGGER_NAME).info(SomeEvent(...))`, so this
    adapter is a `Handler` whose `emit()` unpacks that object instead of
    formatting it:

        from logquill import Logger, RunPlugin
        from logquill.adapters.autogen import AutoGenAdapter

        log = Logger("app")
        # active as soon as it's constructed
        adapter = AutoGenAdapter(log.child("agent").use(RunPlugin()))

    **Only covers (Microsoft) `autogen-core`/`autogen-agentchat` — not
    AG2.** AG2 forked from AutoGen and, as of its 2026 rewrite, moved onto
    its own event-driven architecture (a "MemoryStream pub/sub event bus")
    that no longer shares `autogen_core.EVENT_LOGGER_NAME` or any of the
    event classes below (confirmed against the AG2 source: zero references
    to `EVENT_LOGGER_NAME` in its repository). Unlike LangGraph sharing
    LangChain's callback system, this is a real divergence, not a detail —
    an AG2 adapter needs its own research and its own adapter, not this one
    pointed at a different package name.

    **Weaker correlation than the other adapters, and worth knowing before
    relying on it**: `autogen_core`'s structured events carry an `agent_id`
    (or `sender`/`receiver` for message events) but no call-level
    `span_id`/`parent_span_id`-equivalent — unlike LangChain's `run_id`/
    `parent_run_id`, CrewAI's `event_id`/`parent_event_id`, or LlamaIndex's
    span ids. Each event here becomes a flat `.action()`/`.observation()`/
    `.error()` record carrying whatever fields AutoGen put on it; there's
    no tree to reconstruct from `span_id`/`parent_span_id` the way there is
    for the other three adapters. (AutoGen's *separate* native OpenTelemetry
    tracing, via a `tracer_provider` passed to the agent runtime, does carry
    real span hierarchy — pair it with `TraceContextPlugin`, which already
    reads the active OTel span, if that's what you need; this adapter is
    for the structured *event* stream specifically.)

    `autogen-core` is never imported unless you import
    `logquill.adapters.autogen` yourself.
    """

    def __init__(self, agent_log: Logger) -> None:
        """Attaches this adapter as a `logging.Handler` on AutoGen's
        `EVENT_LOGGER_NAME` logger immediately — active as soon as it's
        constructed, no separate "start" call needed."""
        LogQuillAdapter.__init__(self, agent_log)
        logging.Handler.__init__(self)
        self._event_logger = logging.getLogger(EVENT_LOGGER_NAME)
        # AutoGen's own events are logged at INFO; a logger's effective
        # level defaults to WARNING, which would otherwise filter every one
        # of them out before `emit()` is ever called.
        self._event_logger.setLevel(logging.INFO)
        self._event_logger.addHandler(self)

    def close(self) -> None:
        """Detach this handler from AutoGen's event logger, then run the
        base `logging.Handler.close()`."""
        self._event_logger.removeHandler(self)
        super().close()

    def emit(self, record: logging.LogRecord) -> None:
        """Unpack an `autogen_core` structured event object off `record.msg`
        and forward it as the matching `.action()`/`.observation()`/
        `.error()` call; silently ignored if `record.msg` isn't one of
        those structured event objects (e.g. an ordinary log line)."""
        kwargs = getattr(record.msg, "kwargs", None)
        if not isinstance(kwargs, dict):
            return  # not one of autogen_core.logging's structured event objects
        event_type = kwargs.get("type")
        meta: dict[str, Any] = {k: v for k, v in kwargs.items() if k != "type"}

        if event_type in _ERROR_EVENT_NAMES:
            meta["error"] = meta.pop("exception", "")
            self.log.error(_ERROR_EVENT_NAMES[event_type], **meta)
        elif event_type == "LLMStreamStart":
            self.log.action("llm_stream", **meta)
        elif event_type == "LLMStreamEnd":
            self.log.observation("llm_stream", **meta)
        elif event_type == "LLMCall":
            self.log.observation("llm_call", **meta)
        elif event_type == "ToolCall":
            self.log.observation("tool_call", **meta)
        elif event_type == "Message":
            self.log.action("message", **meta)
        elif event_type == "MessageDropped":
            self.log.observation("message_dropped", **meta)
        else:
            self.log.action(event_type or "autogen_event", **meta)
