import importlib
import sys
import types
import uuid
from types import ModuleType

import pytest

from logquill.core.logger import Logger
from logquill.plugins.run_plugin import RunPlugin
from logquill.transports.transport import CollectingTransport


def _install_fake_langchain_core(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fakes injected via sys.modules — the same pattern this repo already
    # uses for other optional-dependency drivers (see PIIRedactPlugin's
    # presidio tests) — so this exercises the real adapter code against a
    # stand-in `BaseCallbackHandler` without requiring the actual, heavier
    # `langchain-core` package to be installed.
    class FakeBaseCallbackHandler:
        pass

    callbacks_module = types.ModuleType("langchain_core.callbacks")
    callbacks_module.BaseCallbackHandler = FakeBaseCallbackHandler  # type: ignore[attr-defined]

    langchain_core_module = types.ModuleType("langchain_core")
    langchain_core_module.callbacks = callbacks_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "langchain_core", langchain_core_module)
    monkeypatch.setitem(sys.modules, "langchain_core.callbacks", callbacks_module)


def _load_adapter_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    _install_fake_langchain_core(monkeypatch)
    module = importlib.import_module("logquill.adapters.langchain")
    return importlib.reload(module)


def test_raises_an_actionable_error_without_langchain_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "langchain_core", None)
    monkeypatch.delitem(sys.modules, "logquill.adapters.langchain", raising=False)

    with pytest.raises(ImportError, match=r"logquill\[langchain\]"):
        importlib.import_module("logquill.adapters.langchain")


def test_full_run_reconstructs_span_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_adapter_module(monkeypatch)
    LangChainAdapter = module.LangChainAdapter

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink], plugins=[RunPlugin(run_id="run-1")])
    handler = LangChainAdapter(logger)

    chain_run = uuid.uuid4()
    llm_run = uuid.uuid4()
    tool_run = uuid.uuid4()

    handler.on_chain_start({"name": "agent_chain"}, {"input": "hi"}, run_id=chain_run)
    handler.on_llm_start({"name": "llm"}, ["hi"], run_id=llm_run, parent_run_id=chain_run)
    handler.on_llm_end(object(), run_id=llm_run, parent_run_id=chain_run)
    handler.on_tool_start({"name": "search"}, "query", run_id=tool_run, parent_run_id=chain_run)
    handler.on_tool_end("result", run_id=tool_run, parent_run_id=chain_run)
    handler.on_agent_finish(object(), run_id=chain_run)
    handler.on_chain_end({"output": "done"}, run_id=chain_run)

    kinds = [r["meta"]["kind"] for r in sink.records]
    assert kinds == ["action", "observation", "action", "observation", "decision", "span"]

    llm_start, llm_end, tool_start, tool_end, finish, chain_close = sink.records

    # 5+ steps, at least one nested span — sorted by (parent_span_id,
    # span_id) reconstructs the exact call tree.
    assert llm_start["meta"]["span_id"] == str(llm_run)
    assert llm_start["meta"]["parent_span_id"] == str(chain_run)
    assert llm_end["meta"]["span_id"] == str(llm_run)
    assert isinstance(llm_end["meta"]["duration_ms"], float)

    assert tool_start["meta"]["span_id"] == str(tool_run)
    assert tool_start["meta"]["parent_span_id"] == str(chain_run)
    assert isinstance(tool_end["meta"]["duration_ms"], float)

    # on_agent_finish shares the chain's own run_id rather than minting a
    # fresh one, so it's linked via the ambient span (parent_span_id), not
    # given a span_id of its own.
    assert "span_id" not in finish["meta"]
    assert finish["meta"]["parent_span_id"] == str(chain_run)

    assert chain_close["meta"]["span_id"] == str(chain_run)
    assert "parent_span_id" not in chain_close["meta"]
    assert isinstance(chain_close["meta"]["duration_ms"], float)

    # every record shares the run_id `RunPlugin` attached to the logger
    assert {r["meta"]["run_id"] for r in sink.records} == {"run-1"}


def test_nested_chains_link_via_parent_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_adapter_module(monkeypatch)
    LangChainAdapter = module.LangChainAdapter

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    handler = LangChainAdapter(logger)

    outer = uuid.uuid4()
    inner = uuid.uuid4()

    handler.on_chain_start({"name": "outer"}, {}, run_id=outer)
    handler.on_chain_start({"name": "inner"}, {}, run_id=inner, parent_run_id=outer)
    handler.on_chain_end({}, run_id=inner, parent_run_id=outer)
    handler.on_chain_end({}, run_id=outer)

    inner_close, outer_close = sink.records
    assert inner_close["meta"]["span_id"] == str(inner)
    assert inner_close["meta"]["parent_span_id"] == str(outer)
    assert outer_close["meta"]["span_id"] == str(outer)
    assert "parent_span_id" not in outer_close["meta"]


def test_chain_error_closes_the_span_at_error_level(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_adapter_module(monkeypatch)
    LangChainAdapter = module.LangChainAdapter

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    handler = LangChainAdapter(logger)

    run_id = uuid.uuid4()
    handler.on_chain_start({"name": "chain"}, {}, run_id=run_id)
    handler.on_chain_error(RuntimeError("boom"), run_id=run_id)

    assert len(sink.records) == 1
    record = sink.records[0]
    assert record["level"] == "ERROR"
    assert "boom" in record["meta"]["error"]


def test_tool_error_logs_at_error_level(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_adapter_module(monkeypatch)
    LangChainAdapter = module.LangChainAdapter

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    handler = LangChainAdapter(logger)

    run_id = uuid.uuid4()
    handler.on_tool_start({"name": "search"}, "query", run_id=run_id)
    handler.on_tool_error(RuntimeError("tool broke"), run_id=run_id)

    assert len(sink.records) == 2
    error_record = sink.records[1]
    assert error_record["level"] == "ERROR"
    assert error_record["meta"]["error"] == "tool broke"
    assert error_record["meta"]["span_id"] == str(run_id)
