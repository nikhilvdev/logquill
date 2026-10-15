from __future__ import annotations

import importlib
import sys
import types
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import ModuleType

import pytest

from logquill.core.logger import Logger
from logquill.plugins.run_plugin import RunPlugin
from logquill.transports.transport import CollectingTransport


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _install_fake_crewai(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Fakes injected via sys.modules — the same pattern this repo already
    uses for other optional-dependency drivers (see `PIIRedactPlugin`'s
    Presidio tests, `LangChainAdapter`'s tests) — so this exercises the real
    adapter code against a stand-in `crewai.events` module without requiring
    the actual, much heavier `crewai` package to be installed.

    A minimal event bus and set of event dataclasses, shaped like CrewAI's
    real ones closely enough for `CrewAIAdapter` to run unmodified: each
    event already carries `event_id`/`parent_event_id`/`started_event_id`
    the way the real event bus computes and assigns them (confirmed against
    `crewai/events/event_bus.py` and `event_context.py` upstream) — the fake
    doesn't recompute that pairing itself, tests just set the fields
    directly, the same way the LangChain adapter tests hand in `run_id`/
    `parent_run_id` directly rather than driving a real callback manager.
    """

    class FakeEventBus:
        def __init__(self) -> None:
            self._handlers: dict[type, list] = {}

        def on(self, event_cls: type):
            def register(fn):
                self._handlers.setdefault(event_cls, []).append(fn)
                return fn

            return register

        def emit(self, source, event) -> None:
            for fn in self._handlers.get(type(event), []):
                fn(source, event)

    class BaseEventListener:
        def __init__(self) -> None:
            self.setup_listeners(bus)

        def setup_listeners(self, crewai_event_bus) -> None:  # pragma: no cover - abstract
            raise NotImplementedError

    @dataclass
    class FakeEvent:
        event_id: str
        parent_event_id: str | None = None
        started_event_id: str | None = None
        timestamp: datetime = field(default_factory=_now)

    @dataclass
    class CrewKickoffStartedEvent(FakeEvent):
        crew_name: str | None = None

    @dataclass
    class CrewKickoffCompletedEvent(FakeEvent):
        pass

    @dataclass
    class CrewKickoffFailedEvent(FakeEvent):
        error: str = ""

    @dataclass
    class TaskStartedEvent(FakeEvent):
        task_name: str | None = None
        task_id: str | None = None

    @dataclass
    class TaskCompletedEvent(FakeEvent):
        pass

    @dataclass
    class TaskFailedEvent(FakeEvent):
        error: str = ""
        error_type: type | None = None

    @dataclass
    class FakeAgent:
        role: str

    @dataclass
    class AgentExecutionStartedEvent(FakeEvent):
        agent: FakeAgent | None = None

    @dataclass
    class AgentExecutionCompletedEvent(FakeEvent):
        agent: FakeAgent | None = None

    @dataclass
    class AgentExecutionErrorEvent(FakeEvent):
        agent: FakeAgent | None = None
        error: str = ""

    @dataclass
    class ToolUsageStartedEvent(FakeEvent):
        tool_name: str = ""

    @dataclass
    class ToolUsageFinishedEvent(FakeEvent):
        tool_name: str = ""
        started_at: datetime = field(default_factory=_now)
        finished_at: datetime = field(default_factory=_now)

    @dataclass
    class ToolUsageErrorEvent(FakeEvent):
        tool_name: str = ""
        error: object = ""

    @dataclass
    class LLMCallStartedEvent(FakeEvent):
        pass

    @dataclass
    class LLMCallCompletedEvent(FakeEvent):
        pass

    @dataclass
    class LLMCallFailedEvent(FakeEvent):
        error: str = ""

    bus = FakeEventBus()

    events_module = types.ModuleType("crewai.events")
    for name, obj in {
        "BaseEventListener": BaseEventListener,
        "CrewKickoffStartedEvent": CrewKickoffStartedEvent,
        "CrewKickoffCompletedEvent": CrewKickoffCompletedEvent,
        "CrewKickoffFailedEvent": CrewKickoffFailedEvent,
        "TaskStartedEvent": TaskStartedEvent,
        "TaskCompletedEvent": TaskCompletedEvent,
        "TaskFailedEvent": TaskFailedEvent,
        "AgentExecutionStartedEvent": AgentExecutionStartedEvent,
        "AgentExecutionCompletedEvent": AgentExecutionCompletedEvent,
        "AgentExecutionErrorEvent": AgentExecutionErrorEvent,
        "ToolUsageStartedEvent": ToolUsageStartedEvent,
        "ToolUsageFinishedEvent": ToolUsageFinishedEvent,
        "ToolUsageErrorEvent": ToolUsageErrorEvent,
        "LLMCallStartedEvent": LLMCallStartedEvent,
        "LLMCallCompletedEvent": LLMCallCompletedEvent,
        "LLMCallFailedEvent": LLMCallFailedEvent,
    }.items():
        setattr(events_module, name, obj)
    # exposed for tests to drive directly, and to build fake events/agents
    events_module.FakeAgent = FakeAgent  # type: ignore[attr-defined]
    events_module.bus = bus  # type: ignore[attr-defined]

    crewai_module = types.ModuleType("crewai")
    crewai_module.events = events_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "crewai", crewai_module)
    monkeypatch.setitem(sys.modules, "crewai.events", events_module)
    return events_module


def _load_adapter_module(monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, ModuleType]:
    fake_events = _install_fake_crewai(monkeypatch)
    module = importlib.import_module("logquill.adapters.crewai")
    return importlib.reload(module), fake_events


def test_raises_an_actionable_error_without_crewai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "crewai", None)
    monkeypatch.delitem(sys.modules, "logquill.adapters.crewai", raising=False)

    with pytest.raises(ImportError, match=r"logquill\[crewai\]"):
        importlib.import_module("logquill.adapters.crewai")


def test_full_run_reconstructs_span_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    module, fake = _load_adapter_module(monkeypatch)
    CrewAIAdapter = module.CrewAIAdapter

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink], plugins=[RunPlugin(run_id="run-1")])
    CrewAIAdapter(logger)  # registers itself with the fake bus on construction

    crew_start = fake.CrewKickoffStartedEvent(event_id="crew-1", crew_name="research")
    fake.bus.emit(None, crew_start)

    task_start = fake.TaskStartedEvent(
        event_id="task-1", parent_event_id="crew-1", task_name="gather sources"
    )
    fake.bus.emit(None, task_start)

    agent = fake.FakeAgent(role="researcher")
    t0 = _now()
    agent_start = fake.AgentExecutionStartedEvent(
        event_id="agent-1", parent_event_id="task-1", agent=agent, timestamp=t0
    )
    fake.bus.emit(None, agent_start)

    tool_start = fake.ToolUsageStartedEvent(
        event_id="tool-1", parent_event_id="agent-1", tool_name="search"
    )
    fake.bus.emit(None, tool_start)

    started_at = _now()
    finished_at = started_at + timedelta(milliseconds=50)
    tool_end = fake.ToolUsageFinishedEvent(
        event_id="tool-2",
        parent_event_id="agent-1",
        started_event_id="tool-1",
        tool_name="search",
        started_at=started_at,
        finished_at=finished_at,
    )
    fake.bus.emit(None, tool_end)

    agent_end = fake.AgentExecutionCompletedEvent(
        event_id="agent-2",
        parent_event_id="task-1",
        started_event_id="agent-1",
        agent=agent,
        timestamp=t0 + timedelta(milliseconds=100),
    )
    fake.bus.emit(None, agent_end)

    task_end = fake.TaskCompletedEvent(
        event_id="task-2", parent_event_id="crew-1", started_event_id="task-1"
    )
    fake.bus.emit(None, task_end)

    crew_end = fake.CrewKickoffCompletedEvent(event_id="crew-2", started_event_id="crew-1")
    fake.bus.emit(None, crew_end)

    kinds = [r["meta"]["kind"] for r in sink.records]
    assert kinds == ["action", "action", "observation", "observation", "span", "span"]

    agent_action, tool_action, tool_obs, agent_obs, task_close, crew_close = sink.records

    # tool is nested under the agent step, agent under the task, task under the crew
    assert agent_action["meta"]["span_id"] == "agent-1"
    assert agent_action["meta"]["parent_span_id"] == "task-1"

    assert tool_action["meta"]["span_id"] == "tool-1"
    assert tool_action["meta"]["parent_span_id"] == "agent-1"
    assert tool_obs["meta"]["span_id"] == "tool-1"
    assert isinstance(tool_obs["meta"]["duration_ms"], float)
    assert tool_obs["meta"]["duration_ms"] == pytest.approx(50, abs=1)

    assert agent_obs["meta"]["span_id"] == "agent-1"
    assert agent_obs["meta"]["parent_span_id"] == "task-1"
    assert agent_obs["meta"]["duration_ms"] == pytest.approx(100, abs=1)

    assert task_close["meta"]["span_id"] == "task-1"
    assert task_close["meta"]["parent_span_id"] == "crew-1"

    assert crew_close["meta"]["span_id"] == "crew-1"
    assert "parent_span_id" not in crew_close["meta"]

    assert {r["meta"]["run_id"] for r in sink.records} == {"run-1"}


def test_task_failed_uses_the_events_own_error_type(monkeypatch: pytest.MonkeyPatch) -> None:
    module, fake = _load_adapter_module(monkeypatch)
    CrewAIAdapter = module.CrewAIAdapter

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    CrewAIAdapter(logger)

    fake.bus.emit(None, fake.TaskStartedEvent(event_id="task-1"))
    fake.bus.emit(
        None,
        fake.TaskFailedEvent(
            event_id="task-2",
            started_event_id="task-1",
            error="budget exceeded",
            error_type=ValueError,
        ),
    )

    assert len(sink.records) == 1
    record = sink.records[0]
    assert record["level"] == "ERROR"
    assert record["meta"]["error"] == "ValueError: budget exceeded"


def test_crew_failed_without_error_type_falls_back_to_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, fake = _load_adapter_module(monkeypatch)
    CrewAIAdapter = module.CrewAIAdapter

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    CrewAIAdapter(logger)

    fake.bus.emit(None, fake.CrewKickoffStartedEvent(event_id="crew-1"))
    fake.bus.emit(
        None,
        fake.CrewKickoffFailedEvent(event_id="crew-2", started_event_id="crew-1", error="boom"),
    )

    assert len(sink.records) == 1
    record = sink.records[0]
    assert record["level"] == "ERROR"
    assert record["meta"]["error"] == "RuntimeError: boom"


def test_ended_event_with_no_matching_start_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    module, fake = _load_adapter_module(monkeypatch)
    CrewAIAdapter = module.CrewAIAdapter

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    CrewAIAdapter(logger)

    # no matching TaskStartedEvent was ever seen (e.g. listener attached mid-run)
    fake.bus.emit(None, fake.TaskCompletedEvent(event_id="task-2", started_event_id="task-1"))

    assert sink.records == []


def test_llm_call_records_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    module, fake = _load_adapter_module(monkeypatch)
    CrewAIAdapter = module.CrewAIAdapter

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    CrewAIAdapter(logger)

    t0 = _now()
    fake.bus.emit(None, fake.LLMCallStartedEvent(event_id="llm-1", timestamp=t0))
    fake.bus.emit(
        None,
        fake.LLMCallCompletedEvent(
            event_id="llm-2", started_event_id="llm-1", timestamp=t0 + timedelta(milliseconds=30)
        ),
    )

    start_record, end_record = sink.records
    assert start_record["meta"]["kind"] == "action"
    assert end_record["meta"]["kind"] == "observation"
    assert end_record["meta"]["span_id"] == "llm-1"
    assert end_record["meta"]["duration_ms"] == pytest.approx(30, abs=1)
