from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime
from types import ModuleType

import pytest

from logquill.core.logger import Logger
from logquill.transports.transport import CollectingTransport


def _install_fake_llama_index(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Fakes injected via sys.modules — the same pattern used for the other
    optional-dependency adapters in this repo (`LangChainAdapter`,
    `CrewAIAdapter`). Mirrors the real shape closely enough for
    `LlamaIndexAdapter` to run unmodified: `SimpleSpanHandler` tracks
    open/completed spans and computes `duration`/`parent_id` the way
    `llama_index_instrumentation.span_handlers.simple.SimpleSpanHandler`
    does (confirmed against upstream source), and `BaseEventHandler` just
    needs a `handle(event)` entry point.
    """

    pydantic_module = types.ModuleType("pydantic")

    class FakeBaseModel:
        def __init__(self, **kwargs: object) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def fake_config_dict(**kwargs: object) -> dict:
        return dict(kwargs)

    pydantic_module.BaseModel = FakeBaseModel  # type: ignore[attr-defined]
    pydantic_module.ConfigDict = fake_config_dict  # type: ignore[attr-defined]

    class FakeSimpleSpan:
        def __init__(self, id_: str, parent_id: str | None) -> None:
            self.id_ = id_
            self.parent_id = parent_id
            self.start_time = datetime.now()
            self.end_time: datetime | None = None
            self.duration = 0.0

    class FakeSimpleSpanHandler(FakeBaseModel):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)
            self.open_spans: dict[str, FakeSimpleSpan] = {}

        def span_enter(self, id_: str, parent_id: str | None = None, **kwargs: object) -> None:
            self.open_spans[id_] = self.new_span(id_, None, parent_span_id=parent_id)

        def span_exit(self, id_: str, **kwargs: object) -> None:
            self.prepare_to_exit_span(id_, None, None, None)
            self.open_spans.pop(id_, None)

        def span_drop(self, id_: str, err: BaseException | None = None, **kwargs: object) -> None:
            self.prepare_to_drop_span(id_, None, None, err)
            self.open_spans.pop(id_, None)

        # Signatures below match the real `BaseSpanHandler`/`SimpleSpanHandler`
        # shape exactly (`id_, bound_args, instance=None, result=None/err=None`)
        # since `_SpanLogger.prepare_to_exit_span`/`prepare_to_drop_span` call
        # `super().prepare_to_exit_span(id_, bound_args, instance, result, ...)`
        # positionally — a fake missing the `instance` slot would silently
        # shift `result`/`err` into the wrong parameter.
        def new_span(
            self,
            id_: str,
            bound_args: object,
            instance: object = None,
            parent_span_id: str | None = None,
            **kwargs: object,
        ) -> FakeSimpleSpan:
            return FakeSimpleSpan(id_, parent_span_id)

        def prepare_to_exit_span(
            self,
            id_: str,
            bound_args: object,
            instance: object = None,
            result: object = None,
            **kwargs: object,
        ) -> FakeSimpleSpan:
            span = self.open_spans[id_]
            span.end_time = datetime.now()
            span.duration = (span.end_time - span.start_time).total_seconds()
            return span

        def prepare_to_drop_span(
            self,
            id_: str,
            bound_args: object,
            instance: object = None,
            err: BaseException | None = None,
            **kwargs: object,
        ) -> FakeSimpleSpan | None:
            return self.open_spans.get(id_)

    class FakeBaseEventHandler(FakeBaseModel):
        def handle(self, event: object, **kwargs: object) -> object:  # pragma: no cover
            raise NotImplementedError

    class FakeDispatcher:
        def __init__(self) -> None:
            self.span_handlers: list = []
            self.event_handlers: list = []

        def add_span_handler(self, handler: object) -> None:
            self.span_handlers.append(handler)

        def add_event_handler(self, handler: object) -> None:
            self.event_handlers.append(handler)

    dispatcher = FakeDispatcher()

    instrumentation_module = types.ModuleType("llama_index.core.instrumentation")
    instrumentation_module.get_dispatcher = lambda: dispatcher  # type: ignore[attr-defined]

    event_handlers_module = types.ModuleType("llama_index.core.instrumentation.event_handlers")
    event_handlers_module.BaseEventHandler = FakeBaseEventHandler  # type: ignore[attr-defined]

    span_handlers_module = types.ModuleType("llama_index.core.instrumentation.span_handlers")
    span_handlers_module.SimpleSpanHandler = FakeSimpleSpanHandler  # type: ignore[attr-defined]

    core_module = types.ModuleType("llama_index.core")
    core_module.instrumentation = instrumentation_module  # type: ignore[attr-defined]
    llama_index_module = types.ModuleType("llama_index")
    llama_index_module.core = core_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "pydantic", pydantic_module)
    monkeypatch.setitem(sys.modules, "llama_index", llama_index_module)
    monkeypatch.setitem(sys.modules, "llama_index.core", core_module)
    monkeypatch.setitem(sys.modules, "llama_index.core.instrumentation", instrumentation_module)
    monkeypatch.setitem(
        sys.modules, "llama_index.core.instrumentation.event_handlers", event_handlers_module
    )
    monkeypatch.setitem(
        sys.modules, "llama_index.core.instrumentation.span_handlers", span_handlers_module
    )

    instrumentation_module.dispatcher = dispatcher  # type: ignore[attr-defined]
    return instrumentation_module


def _load_adapter_module(monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, ModuleType]:
    fake = _install_fake_llama_index(monkeypatch)
    module = importlib.import_module("logquill.adapters.llamaindex")
    return importlib.reload(module), fake


def test_raises_an_actionable_error_without_llama_index(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "llama_index", None)
    monkeypatch.delitem(sys.modules, "logquill.adapters.llamaindex", raising=False)

    with pytest.raises(ImportError, match=r"logquill\[llamaindex\]"):
        importlib.import_module("logquill.adapters.llamaindex")


def test_constructing_registers_both_handlers_with_the_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, fake = _load_adapter_module(monkeypatch)

    logger = Logger("app.agent")
    adapter = module.LlamaIndexAdapter(logger)

    assert fake.dispatcher.span_handlers == [adapter._span_handler]
    assert fake.dispatcher.event_handlers == [adapter._event_handler]


def test_span_close_carries_duration_and_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    module, _fake = _load_adapter_module(monkeypatch)

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    span_handler = module._SpanLogger(log=logger)

    span_handler.span_enter("query.query-abc123", parent_id=None)
    span_handler.span_enter("retriever.retrieve-def456", parent_id="query.query-abc123")
    span_handler.span_exit("retriever.retrieve-def456")
    span_handler.span_exit("query.query-abc123")

    inner_close, outer_close = sink.records
    assert inner_close["message"] == "retriever.retrieve"
    assert inner_close["meta"]["span_id"] == "retriever.retrieve-def456"
    assert inner_close["meta"]["parent_span_id"] == "query.query-abc123"
    assert isinstance(inner_close["meta"]["duration_ms"], float)
    assert inner_close["meta"]["kind"] == "span"

    assert outer_close["message"] == "query.query"
    assert outer_close["meta"]["span_id"] == "query.query-abc123"
    assert "parent_span_id" not in outer_close["meta"]


def test_dropped_span_logs_at_error_level(monkeypatch: pytest.MonkeyPatch) -> None:
    module, _fake = _load_adapter_module(monkeypatch)

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    span_handler = module._SpanLogger(log=logger)

    span_handler.span_enter("query.query-abc123", parent_id=None)
    span_handler.span_drop("query.query-abc123", err=ValueError("boom"))

    assert len(sink.records) == 1
    record = sink.records[0]
    assert record["level"] == "ERROR"
    assert record["meta"]["error"] == "ValueError: boom"


class _FakeEvent:
    def __init__(self, class_name: str, span_id: str | None = None, **extra: object) -> None:
        self._class_name = class_name
        self.span_id = span_id
        for key, value in extra.items():
            setattr(self, key, value)

    def class_name(self) -> str:
        return self._class_name


def test_event_handler_classifies_by_name_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    module, _fake = _load_adapter_module(monkeypatch)

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    event_handler = module._EventLogger(log=logger)

    event_handler.handle(_FakeEvent("LLMChatStartEvent", span_id="span-1"))
    event_handler.handle(_FakeEvent("LLMChatEndEvent", span_id="span-1"))
    event_handler.handle(
        _FakeEvent("StreamChatErrorEvent", span_id="span-1", exception=RuntimeError("x"))
    )
    event_handler.handle(_FakeEvent("LLMChatInProgressEvent", span_id="span-1"))  # skipped
    event_handler.handle(_FakeEvent("AgentToolCallEvent", span_id="span-1"))  # no Start/End suffix

    assert len(sink.records) == 4  # the InProgress event is dropped

    start, end, error, tool_call = sink.records
    assert start["meta"]["kind"] == "action"
    assert start["meta"]["parent_span_id"] == "span-1"
    assert end["meta"]["kind"] == "observation"
    assert error["level"] == "ERROR"
    assert error["meta"]["error"] == "x"
    assert tool_call["meta"]["kind"] == "action"  # generic fallback


def test_event_without_a_span_id_carries_no_parent_span_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, _fake = _load_adapter_module(monkeypatch)

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    event_handler = module._EventLogger(log=logger)

    event_handler.handle(_FakeEvent("EmbeddingStartEvent", span_id=None))

    assert "parent_span_id" not in sink.records[0]["meta"]
