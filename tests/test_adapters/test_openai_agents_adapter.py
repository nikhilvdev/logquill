from __future__ import annotations

import asyncio
import importlib
import sys
import types
from types import ModuleType

import pytest

from logquill.core.logger import Logger
from logquill.transports.transport import CollectingTransport


class FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeResponse:
    def __init__(self, input_tokens: int = 10, output_tokens: int = 4) -> None:
        self.usage = FakeUsage(input_tokens, output_tokens)


class FakeAgent:
    def __init__(self, name: str, model: str = "gpt-fake") -> None:
        self.name = name
        self.model = model


class FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


def _install_fake_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    # Same `sys.modules` injection pattern as the other framework adapters
    # (see `tests/test_adapters/test_langchain_adapter.py`), so this
    # exercises the real adapter against a stand-in `RunHooks` without
    # requiring the actual, heavier `openai-agents` package.
    class FakeRunHooks:
        pass

    lifecycle_module = types.ModuleType("agents.lifecycle")
    lifecycle_module.RunHooks = FakeRunHooks  # type: ignore[attr-defined]
    agents_module = types.ModuleType("agents")
    agents_module.lifecycle = lifecycle_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "agents", agents_module)
    monkeypatch.setitem(sys.modules, "agents.lifecycle", lifecycle_module)


def _load_adapter_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    _install_fake_agents(monkeypatch)
    monkeypatch.delitem(sys.modules, "logquill.adapters.openai_agents", raising=False)
    return importlib.import_module("logquill.adapters.openai_agents")


def test_raises_an_actionable_error_without_openai_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "agents", None)
    monkeypatch.delitem(sys.modules, "logquill.adapters.openai_agents", raising=False)

    with pytest.raises(ImportError, match=r"logquill\[openai-agents\]"):
        importlib.import_module("logquill.adapters.openai_agents")


def test_a_run_with_a_tool_call_and_an_llm_call_produces_a_full_span_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_adapter_module(monkeypatch)
    OpenAIAgentsAdapter = module.OpenAIAgentsAdapter

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    adapter = OpenAIAgentsAdapter(logger)
    context = object()
    agent = FakeAgent("planner")

    async def run() -> None:
        await adapter.on_agent_start(context, agent)
        await adapter.on_tool_start(context, agent, FakeTool("search"))
        await adapter.on_tool_end(context, agent, FakeTool("search"), "result")
        await adapter.on_llm_start(context, agent, None, [])
        await adapter.on_llm_end(context, agent, FakeResponse(input_tokens=100, output_tokens=25))
        await adapter.on_agent_end(context, agent, "done")

    asyncio.run(run())

    kinds = [r["meta"]["kind"] for r in sink.records]
    assert kinds == ["action", "observation", "action", "span"]

    action, observation, llm_action, span = sink.records
    assert action["meta"]["tool"] == "search"
    assert isinstance(observation["meta"]["duration_ms"], float)
    assert observation["meta"]["tool"] == "search"
    assert llm_action["llm"] == {
        "model": "gpt-fake",
        "tokens_in": 100,
        "tokens_out": 25,
        "latency_ms": llm_action["llm"]["latency_ms"],
    }
    assert span["message"] == "planner"
    assert span["meta"]["operation"] == "invoke_agent"
    assert span["meta"]["agent_name"] == "planner"
    assert isinstance(span["meta"]["duration_ms"], float)

    # zero-effort nesting: every event happened inside the agent's span
    assert action["meta"]["parent_span_id"] == span["meta"]["span_id"]
    assert observation["meta"]["parent_span_id"] == span["meta"]["span_id"]
    assert llm_action["meta"]["parent_span_id"] == span["meta"]["span_id"]


def test_a_handoff_opens_a_nested_span_for_the_new_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_adapter_module(monkeypatch)
    OpenAIAgentsAdapter = module.OpenAIAgentsAdapter

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    adapter = OpenAIAgentsAdapter(logger)
    context = object()
    planner, specialist = FakeAgent("planner"), FakeAgent("specialist")

    async def run() -> None:
        await adapter.on_agent_start(context, planner)
        await adapter.on_handoff(context, planner, specialist)
        await adapter.on_agent_start(context, specialist)
        await adapter.on_agent_end(context, specialist, "done")
        await adapter.on_agent_end(context, planner, "done")

    asyncio.run(run())

    decision, inner_span, outer_span = sink.records
    assert decision["meta"]["kind"] == "decision"
    assert decision["meta"]["from_agent"] == "planner"
    assert decision["meta"]["to_agent"] == "specialist"
    assert inner_span["message"] == "specialist"
    assert outer_span["message"] == "planner"
    assert inner_span["meta"]["parent_span_id"] == outer_span["meta"]["span_id"]
    assert "parent_span_id" not in outer_span["meta"]


def test_two_runs_gathered_concurrently_do_not_share_span_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_adapter_module(monkeypatch)
    OpenAIAgentsAdapter = module.OpenAIAgentsAdapter

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    adapter = OpenAIAgentsAdapter(logger)

    async def one_run(context: object, name: str) -> None:
        await adapter.on_agent_start(context, FakeAgent(name))
        await adapter.on_tool_start(context, FakeAgent(name), FakeTool("search"))
        await asyncio.sleep(0)  # yield control, so the two runs genuinely interleave
        await adapter.on_tool_end(context, FakeAgent(name), FakeTool("search"), "r")
        await adapter.on_agent_end(context, FakeAgent(name), "done")

    async def run_both() -> None:
        await asyncio.gather(one_run(object(), "a"), one_run(object(), "b"))

    asyncio.run(run_both())

    by_message: dict[str, list[dict]] = {}
    for record in sink.records:
        by_message.setdefault(record["message"], []).append(record)
    a_action, b_action = by_message["call search"]
    a_span, b_span = by_message["a"][0], by_message["b"][0]

    # each tool call is parented to its own run's span, never the other run's
    assert a_action["meta"]["parent_span_id"] == a_span["meta"]["span_id"]
    assert b_action["meta"]["parent_span_id"] == b_span["meta"]["span_id"]
    assert "parent_span_id" not in a_span["meta"]
    assert "parent_span_id" not in b_span["meta"]
    assert adapter._agent_spans == {}


def test_llm_call_without_a_matching_start_has_no_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_adapter_module(monkeypatch)

    sink = CollectingTransport()
    adapter = module.OpenAIAgentsAdapter(Logger("app", transports=[sink]))

    asyncio.run(adapter.on_llm_end(object(), FakeAgent("a"), FakeResponse()))

    assert "latency_ms" not in sink.records[0]["llm"]
