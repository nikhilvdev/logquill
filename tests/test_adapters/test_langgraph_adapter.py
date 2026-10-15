from __future__ import annotations

import importlib
import sys
import types
import uuid
from dataclasses import dataclass
from types import ModuleType

import pytest

from logquill.core.logger import Logger
from logquill.transports.transport import CollectingTransport


def _install_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fakes injected via sys.modules — same pattern as `LangChainAdapter`'s
    # own tests. `GraphCallbackHandler` must subclass the *same* fake
    # `BaseCallbackHandler` that `logquill.adapters.langchain` picks up,
    # otherwise `class LangGraphAdapter(LangChainAdapter, GraphCallbackHandler)`
    # would sit on two unrelated "BaseCallbackHandler" classes and Python
    # would refuse to compute a consistent MRO — exactly mirroring how the
    # real `langgraph.callbacks.GraphCallbackHandler` subclasses the real
    # `langchain_core.callbacks.BaseCallbackHandler`.
    class FakeBaseCallbackHandler:
        pass

    class FakeGraphCallbackHandler(FakeBaseCallbackHandler):
        def on_interrupt(self, event: object) -> None:  # pragma: no cover - default no-op
            pass

        def on_resume(self, event: object) -> None:  # pragma: no cover - default no-op
            pass

    callbacks_module = types.ModuleType("langchain_core.callbacks")
    callbacks_module.BaseCallbackHandler = FakeBaseCallbackHandler  # type: ignore[attr-defined]
    langchain_core_module = types.ModuleType("langchain_core")
    langchain_core_module.callbacks = callbacks_module  # type: ignore[attr-defined]

    langgraph_callbacks_module = types.ModuleType("langgraph.callbacks")
    langgraph_callbacks_module.GraphCallbackHandler = FakeGraphCallbackHandler  # type: ignore[attr-defined]
    langgraph_module = types.ModuleType("langgraph")
    langgraph_module.callbacks = langgraph_callbacks_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "langchain_core", langchain_core_module)
    monkeypatch.setitem(sys.modules, "langchain_core.callbacks", callbacks_module)
    monkeypatch.setitem(sys.modules, "langgraph", langgraph_module)
    monkeypatch.setitem(sys.modules, "langgraph.callbacks", langgraph_callbacks_module)


def _load_adapter_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    _install_fakes(monkeypatch)
    langchain_module = importlib.import_module("logquill.adapters.langchain")
    importlib.reload(langchain_module)
    langgraph_module = importlib.import_module("logquill.adapters.langgraph")
    return importlib.reload(langgraph_module)


@dataclass
class _FakeInterrupt:
    id: str
    value: object


@dataclass
class _FakeGraphInterruptEvent:
    run_id: object
    status: str
    checkpoint_id: str
    checkpoint_ns: tuple
    interrupts: tuple


@dataclass
class _FakeGraphResumeEvent:
    run_id: object
    status: str
    checkpoint_id: str
    checkpoint_ns: tuple


def test_raises_an_actionable_error_without_langgraph(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deliberately does *not* call `_install_fakes` first: if it did,
    # `sys.modules["langgraph.callbacks"]` would still hold the valid fake
    # submodule even after the parent `"langgraph"` key below is nulled —
    # Python's import machinery checks the submodule's own cache entry
    # first and would return it without ever noticing the parent is `None`.
    # Nulling both keys directly simulates "genuinely not installed".
    monkeypatch.setitem(sys.modules, "langgraph", None)
    monkeypatch.setitem(sys.modules, "langgraph.callbacks", None)
    monkeypatch.delitem(sys.modules, "logquill.adapters.langgraph", raising=False)

    with pytest.raises(ImportError, match=r"logquill\[langgraph\]"):
        importlib.import_module("logquill.adapters.langgraph")


def test_still_inherits_langchain_event_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_adapter_module(monkeypatch)
    LangGraphAdapter = module.LangGraphAdapter

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    handler = LangGraphAdapter(logger)

    run_id = uuid.uuid4()
    handler.on_llm_start({"name": "llm"}, ["hi"], run_id=run_id)
    handler.on_llm_end(object(), run_id=run_id)

    start, end = sink.records
    assert start["meta"]["kind"] == "action"
    assert end["meta"]["kind"] == "observation"
    assert start["meta"]["span_id"] == str(run_id)


def test_on_interrupt_becomes_an_observation_with_checkpoint_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_adapter_module(monkeypatch)
    LangGraphAdapter = module.LangGraphAdapter

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    handler = LangGraphAdapter(logger)

    run_id = uuid.uuid4()
    event = _FakeGraphInterruptEvent(
        run_id=run_id,
        status="interrupt_before",
        checkpoint_id="chk-1",
        checkpoint_ns=("graph", "subgraph"),
        interrupts=(_FakeInterrupt(id="int-1", value={"question": "approve?"}),),
    )

    handler.on_interrupt(event)

    assert len(sink.records) == 1
    record = sink.records[0]
    assert record["message"] == "graph_interrupted"
    assert record["meta"]["kind"] == "observation"
    assert record["meta"]["checkpoint_id"] == "chk-1"
    assert record["meta"]["status"] == "interrupt_before"
    assert record["meta"]["checkpoint_ns"] == ["graph", "subgraph"]
    assert record["meta"]["parent_span_id"] == str(run_id)
    assert record["meta"]["interrupts"] == [{"id": "int-1", "value": {"question": "approve?"}}]


def test_on_resume_becomes_an_action_with_checkpoint_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_adapter_module(monkeypatch)
    LangGraphAdapter = module.LangGraphAdapter

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    handler = LangGraphAdapter(logger)

    run_id = uuid.uuid4()
    event = _FakeGraphResumeEvent(
        run_id=run_id, status="pending", checkpoint_id="chk-1", checkpoint_ns=()
    )

    handler.on_resume(event)

    assert len(sink.records) == 1
    record = sink.records[0]
    assert record["message"] == "graph_resumed"
    assert record["meta"]["kind"] == "action"
    assert record["meta"]["checkpoint_id"] == "chk-1"
    assert "checkpoint_ns" not in record["meta"]  # empty tuple, nothing to nest under
    assert record["meta"]["parent_span_id"] == str(run_id)


def test_run_id_none_omits_parent_span_id(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_adapter_module(monkeypatch)
    LangGraphAdapter = module.LangGraphAdapter

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    handler = LangGraphAdapter(logger)

    event = _FakeGraphResumeEvent(
        run_id=None, status="pending", checkpoint_id="chk-1", checkpoint_ns=()
    )

    handler.on_resume(event)

    assert "parent_span_id" not in sink.records[0]["meta"]


def test_interrupt_span_shares_the_enclosing_chains_span_via_ambient_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The graph's own on_chain_start opens a span; on_interrupt firing while
    # it's still open (paused, not ended) should see the same run_id either
    # way — this just confirms the two don't conflict when both fire.
    module = _load_adapter_module(monkeypatch)
    LangGraphAdapter = module.LangGraphAdapter

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    handler = LangGraphAdapter(logger)

    chain_run = uuid.uuid4()
    handler.on_chain_start({"name": "graph"}, {}, run_id=chain_run)
    handler.on_interrupt(
        _FakeGraphInterruptEvent(
            run_id=chain_run,
            status="interrupt_before",
            checkpoint_id="chk-1",
            checkpoint_ns=(),
            interrupts=(),
        )
    )
    handler.on_chain_end({}, run_id=chain_run)

    interrupt_record, chain_close = sink.records
    assert interrupt_record["meta"]["parent_span_id"] == str(chain_run)
    assert chain_close["meta"]["span_id"] == str(chain_run)
