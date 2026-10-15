import sys
import types

import pytest

from logquill.core.logger import Logger
from logquill.plugins.trace_context_plugin import (
    TraceContextPlugin,
    parse_trace_header,
    reset_traceparent,
    set_traceparent,
)


def test_generates_a_trace_id_when_nothing_is_available() -> None:
    logger = Logger("app.test", plugins=[TraceContextPlugin()])

    record = logger.info("step")

    assert record is not None
    trace_id = record["meta"]["trace_id"]
    assert isinstance(trace_id, str)
    assert len(trace_id) == 32
    int(trace_id, 16)  # valid hex


def test_does_not_override_an_existing_trace_id() -> None:
    logger = Logger("app.test", plugins=[TraceContextPlugin()])

    record = logger.info("step", trace_id="already-set")

    assert record is not None
    assert record["meta"]["trace_id"] == "already-set"


def test_custom_trace_key() -> None:
    logger = Logger("app.test", plugins=[TraceContextPlugin(trace_key="correlation_id")])

    record = logger.info("step")

    assert record is not None
    assert "correlation_id" in record["meta"]
    assert "trace_id" not in record["meta"]


def test_explicit_traceparent_constructor_arg_is_parsed() -> None:
    header = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    logger = Logger("app.test", plugins=[TraceContextPlugin(traceparent=header)])

    record = logger.info("step")

    assert record is not None
    assert record["meta"]["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"


def test_set_traceparent_propagates_via_contextvar() -> None:
    logger = Logger("app.test", plugins=[TraceContextPlugin()])
    header = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    token = set_traceparent(header)
    try:
        record = logger.info("step")
    finally:
        reset_traceparent(token)

    assert record is not None
    assert record["meta"]["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"

    # after reset, a fresh (different, generated) trace id is used
    record_after = logger.info("step 2")
    assert record_after is not None
    assert record_after["meta"]["trace_id"] != "4bf92f3577b34da6a3ce929d0e0e4736"


class TestParseTraceHeader:
    def test_w3c_traceparent(self) -> None:
        header = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        assert parse_trace_header(header) == "4bf92f3577b34da6a3ce929d0e0e4736"

    def test_aws_xray(self) -> None:
        header = "Root=1-5e1b4151-5ac6c9df6a1c0c8e5c1e1e1e;Parent=53995c3f42cd8ad8;Sampled=1"
        assert parse_trace_header(header) == "5e1b41515ac6c9df6a1c0c8e5c1e1e1e"

    def test_gcp_cloud_trace(self) -> None:
        header = "105445aa7843bc8bf206b12000100000/1;o=1"
        assert parse_trace_header(header) == "105445aa7843bc8bf206b12000100000"

    def test_unrecognized_header_returns_none(self) -> None:
        assert parse_trace_header("not-a-trace-header") is None


def test_active_otel_span_wins_over_header(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSpanContext:
        trace_id = 0x4BF92F3577B34DA6A3CE929D0E0E4736
        is_valid = True

    class FakeSpan:
        def get_span_context(self) -> "FakeSpanContext":
            return FakeSpanContext()

    trace_module = types.ModuleType("opentelemetry.trace")
    trace_module.get_current_span = lambda: FakeSpan()  # type: ignore[attr-defined]
    otel_module = types.ModuleType("opentelemetry")
    otel_module.trace = trace_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "opentelemetry", otel_module)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", trace_module)

    header = "00-11111111111111111111111111111111-00f067aa0ba902b7-01"
    logger = Logger("app.test", plugins=[TraceContextPlugin(traceparent=header)])

    record = logger.info("step")

    assert record is not None
    assert record["meta"]["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"


def test_no_otel_installed_falls_back_to_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "opentelemetry", None)

    header = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    logger = Logger("app.test", plugins=[TraceContextPlugin(traceparent=header)])

    record = logger.info("step")

    assert record is not None
    assert record["meta"]["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
