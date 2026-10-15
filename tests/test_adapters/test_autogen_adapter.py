from __future__ import annotations

import importlib
import logging
import sys
import types
from types import ModuleType

import pytest

from logquill.core.logger import Logger
from logquill.transports.transport import CollectingTransport

_EVENT_LOGGER_NAME = "autogen_core.events"


@pytest.fixture(autouse=True)
def _detach_stray_handlers() -> object:
    # `logging.getLogger(name)` is a real, process-global singleton — every
    # `AutoGenAdapter` constructed below attaches a handler to the *same*
    # logger object across tests. Harmless for each test's own assertions
    # (each handler only ever writes to its own `self.log`), but handlers
    # would otherwise accumulate for the rest of the test session.
    yield
    logging.getLogger(_EVENT_LOGGER_NAME).handlers.clear()


def _install_fake_autogen_core(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Fakes injected via sys.modules — same pattern as the other adapters'
    tests. `EVENT_LOGGER_NAME` is the only symbol `AutoGenAdapter` actually
    imports; everything else (the event objects) is exactly what
    `autogen_core.logging`'s real classes produce — a plain object exposing
    a `.kwargs` dict with a `"type"` key, confirmed against upstream source.
    """
    module = types.ModuleType("autogen_core")
    module.EVENT_LOGGER_NAME = "autogen_core.events"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "autogen_core", module)
    return module


def _load_adapter_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    _install_fake_autogen_core(monkeypatch)
    module = importlib.import_module("logquill.adapters.autogen")
    return importlib.reload(module)


class _FakeEvent:
    """Mirrors the real `autogen_core.logging` event classes' shape: every
    field lives in `.kwargs`, including `"type"`."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


def _emit_event(logger_name: str, event: _FakeEvent) -> None:
    logging.getLogger(logger_name).info(event)


def test_raises_an_actionable_error_without_autogen_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "autogen_core", None)
    monkeypatch.delitem(sys.modules, "logquill.adapters.autogen", raising=False)

    with pytest.raises(ImportError, match=r"logquill\[autogen\]"):
        importlib.import_module("logquill.adapters.autogen")


def test_llm_call_becomes_an_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_adapter_module(monkeypatch)

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    module.AutoGenAdapter(logger)

    _emit_event(
        module.EVENT_LOGGER_NAME,
        _FakeEvent(
            type="LLMCall",
            messages=[{"role": "user", "content": "hi"}],
            response={"content": "hello"},
            prompt_tokens=5,
            completion_tokens=3,
            agent_id="agent-1",
        ),
    )

    assert len(sink.records) == 1
    record = sink.records[0]
    assert record["meta"]["kind"] == "observation"
    assert record["meta"]["prompt_tokens"] == 5
    assert record["meta"]["completion_tokens"] == 3
    assert record["meta"]["agent_id"] == "agent-1"
    assert "type" not in record["meta"]


def test_llm_stream_start_and_end(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_adapter_module(monkeypatch)

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    module.AutoGenAdapter(logger)

    _emit_event(module.EVENT_LOGGER_NAME, _FakeEvent(type="LLMStreamStart", messages=[]))
    _emit_event(
        module.EVENT_LOGGER_NAME,
        _FakeEvent(type="LLMStreamEnd", response={}, prompt_tokens=1, completion_tokens=1),
    )

    start, end = sink.records
    assert start["meta"]["kind"] == "action"
    assert end["meta"]["kind"] == "observation"


def test_message_handler_exception_maps_error_field(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_adapter_module(monkeypatch)

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    module.AutoGenAdapter(logger)

    _emit_event(
        module.EVENT_LOGGER_NAME,
        _FakeEvent(
            type="MessageHandlerException",
            payload="{}",
            handling_agent="agent-1",
            exception="boom",
        ),
    )

    assert len(sink.records) == 1
    record = sink.records[0]
    assert record["level"] == "ERROR"
    assert record["meta"]["error"] == "boom"
    assert "exception" not in record["meta"]


def test_plain_string_log_records_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_adapter_module(monkeypatch)

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    module.AutoGenAdapter(logger)

    # something unrelated logging a plain string to the same logger name
    logging.getLogger(module.EVENT_LOGGER_NAME).info("not a structured event")

    assert sink.records == []


def test_close_detaches_the_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_adapter_module(monkeypatch)

    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    adapter = module.AutoGenAdapter(logger)

    adapter.close()
    _emit_event(module.EVENT_LOGGER_NAME, _FakeEvent(type="Message", payload="hi"))

    assert sink.records == []
