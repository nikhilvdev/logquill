"""Slice 6: what Python gains from the JS package (spec 09, section B)."""

import signal
import subprocess
import sys
import textwrap
import threading
import types

import pytest

from logquill import Logger, install_signal_handlers
from logquill.bridges import loguru_sink, structlog_processor
from logquill.instrument import anthropic, openai
from logquill.tracing.otel_span_processor import OtelSpanProcessor
from logquill.transports.transport import CollectingTransport


def make_logger(**kwargs):
    sink = CollectingTransport()
    return Logger("t", transports=[sink], async_dispatch=False, **kwargs), sink


# ---- Logger(meta=...) -----------------------------------------------------------------------


def test_base_meta_is_on_every_record_and_a_call_can_override_it():
    log, sink = make_logger(meta={"service": "api", "region": "eu"})
    log.info("a")
    log.info("b", region="us")
    assert sink.records[0]["meta"] == {"service": "api", "region": "eu"}
    assert sink.records[1]["meta"] == {"service": "api", "region": "us"}


def test_a_child_inherits_base_meta_and_its_own_wins():
    log, sink = make_logger(meta={"service": "api", "region": "eu"})
    log.child("db", region="us").info("q")
    assert sink.records[0]["meta"] == {"service": "api", "region": "us"}
    log.child("plain").info("q")
    assert sink.records[1]["meta"] == {"service": "api", "region": "eu"}
    # a child's own fixed meta is not handed down (D1), but the root's `meta=` is
    log.child("db", tier="storage").child("pool").info("q")
    assert sink.records[2]["meta"] == {"service": "api", "region": "eu"}


# ---- install_signal_handlers ----------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_sigterm_drains_the_queue_and_exits_with_128_plus_signal(tmp_path):
    log_file = tmp_path / "sig.log"
    script = textwrap.dedent(
        f"""
        import os, signal
        from logquill import Logger, FileTransport, install_signal_handlers
        log = Logger("app", transports=[FileTransport({str(log_file)!r})], flush_at_exit=False)
        install_signal_handlers(log)
        for i in range(300):
            log.info("queued", i=i)
        os.kill(os.getpid(), signal.SIGTERM)
        """
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=30)
    assert result.returncode == 128 + signal.SIGTERM, result.stderr.decode()
    assert len(log_file.read_text().splitlines()) == 300


def test_install_signal_handlers_refuses_a_worker_thread():
    log, _ = make_logger()
    errors = []

    def attempt():
        try:
            install_signal_handlers(log)
        except ValueError as error:
            errors.append(str(error))

    thread = threading.Thread(target=attempt)
    thread.start()
    thread.join()
    assert errors and "main thread" in errors[0]


# ---- LangChain fallback span name -----------------------------------------------------------


def test_the_span_name_falls_back_to_the_last_segment_of_serialized_id(monkeypatch):
    # the adapter imports langchain_core at load; only `BaseCallbackHandler` is needed to import it
    monkeypatch.setitem(
        sys.modules,
        "langchain_core",
        types.SimpleNamespace(callbacks=types.SimpleNamespace(BaseCallbackHandler=object)),
    )
    monkeypatch.setitem(sys.modules, "langchain_core.callbacks", sys.modules["langchain_core"].callbacks)
    monkeypatch.delitem(sys.modules, "logquill.adapters.langchain", raising=False)
    from logquill.adapters.langchain import _tool_name

    assert _tool_name({"name": "named", "id": ["a", "B"]}, "chain") == "named"
    assert _tool_name({"id": ["langchain", "chains", "LLMChain"]}, "chain") == "LLMChain"
    assert _tool_name({"id": []}, "chain") == "chain"
    assert _tool_name(None, "chain") == "chain"
    sys.modules.pop("logquill.adapters.langchain")  # don't leak the stub-backed import


# ---- per-client instrumentation -------------------------------------------------------------


def fake_client(kind):
    """Shaped like the SDKs: `client.<resource>.create`, a method on the resource's class."""

    class Usage:
        prompt_tokens = input_tokens = 3
        completion_tokens = output_tokens = 4

    class Response:
        model = "m"
        usage = Usage()
        choices = [types.SimpleNamespace(finish_reason="stop")]
        status = "completed"
        incomplete_details = None
        stop_reason = "end_turn"

    class Resource:
        def create(self, **kwargs):
            return Response()

    class AsyncResource:
        async def create(self, **kwargs):
            return Response()

    resource = AsyncResource if kind == "async" else Resource
    return types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=resource()),
        responses=resource(),
        messages=resource(),
    )


def create_resource(instrumenter, client):
    return client.messages if instrumenter is anthropic else client.chat.completions


@pytest.mark.parametrize("instrumenter", [openai, anthropic])
def test_a_client_is_instrumented_alone_and_restored(instrumenter):
    log, sink = make_logger()
    mine, other = fake_client("sync"), fake_client("sync")
    instrumenter(log, client=mine)
    try:
        create_resource(instrumenter, mine).create(model="m")
        create_resource(instrumenter, other).create(model="m")
        assert len(sink.records) == 1 and sink.records[0]["llm"]["model"] == "m"
    finally:
        instrumenter.uninstrument()
    assert "create" not in vars(create_resource(instrumenter, mine))
    assert not instrumenter.active


@pytest.mark.parametrize("instrumenter", [openai, anthropic])
def test_several_clients_can_be_instrumented_one_call_after_another(instrumenter):
    log, sink = make_logger()
    a, b = fake_client("sync"), fake_client("sync")
    instrumenter(log, client=a)
    instrumenter(log, client=b)
    try:
        for client in (a, b):
            create_resource(instrumenter, client).create(model="m")
        assert len(sink.records) == 2
    finally:
        instrumenter.uninstrument()


def test_an_async_client_is_awaited_and_logged():
    import asyncio

    log, sink = make_logger()
    client = fake_client("async")
    openai(log, client=client)
    try:
        asyncio.run(client.chat.completions.create(model="m"))
        assert sink.records[0]["llm"]["tokens_in"] == 3
    finally:
        openai.uninstrument()


def test_global_and_per_client_instrumentation_do_not_mix():
    log, _ = make_logger()
    openai(log, client=fake_client("sync"))
    try:
        with pytest.raises(RuntimeError, match="already active"):
            openai(log)
    finally:
        openai.uninstrument()


# ---- OtelSpanProcessor ----------------------------------------------------------------------


def otel_span(*, status=None, attributes=None, parent=None):
    code = types.SimpleNamespace(value=status) if status is not None else None
    return types.SimpleNamespace(
        name="call_llm",
        context=types.SimpleNamespace(span_id=0xAB),
        parent=types.SimpleNamespace(span_id=0xCD) if parent else None,
        attributes=attributes,
        start_time=1_000_000_000,
        end_time=1_250_000_000,
        status=types.SimpleNamespace(status_code=code, description="boom") if code else None,
    )


def test_a_span_becomes_an_action_then_an_observation_with_its_ids_and_duration():
    log, sink = make_logger()
    processor = OtelSpanProcessor(log)
    span = otel_span(parent=True, attributes={"k": "v"})
    processor.on_start(span)
    processor.on_end(span)
    start, end = (r["meta"] for r in sink.records)
    assert start["kind"] == "action" and end["kind"] == "observation"
    assert end["span_id"] == "00000000000000ab" and end["parent_span_id"] == "00000000000000cd"
    assert end["duration_ms"] == 250.0 and end["attributes"] == {"k": "v"}


def test_an_error_status_is_an_error_record_and_attributes_key_is_configurable():
    log, sink = make_logger()
    OtelSpanProcessor(log, attributes_key="otel").on_end(otel_span(status=2, attributes={"k": 1}))
    record = sink.records[0]
    assert record["level"] == "ERROR" and record["meta"]["error"] == "boom"
    assert record["meta"]["otel"] == {"k": 1}


# ---- bridges --------------------------------------------------------------------------------


def test_structlog_processor_forwards_levels_and_meta_then_drops_the_event(monkeypatch):
    class DropEvent(Exception):
        pass

    monkeypatch.setitem(sys.modules, "structlog", types.SimpleNamespace(DropEvent=DropEvent))
    log, sink = make_logger()
    process = structlog_processor(log)
    with pytest.raises(DropEvent):
        process(None, "warning", {"event": "retrying", "attempt": 2, "level": "warning", "timestamp": "x"})
    record = sink.records[0]
    assert (record["level"], record["message"], record["meta"]) == ("WARN", "retrying", {"attempt": 2})


def test_loguru_sink_maps_levels_extra_and_exceptions():
    log, sink = make_logger()
    write = loguru_sink(log)

    def message(no, text, exception=None):
        return types.SimpleNamespace(
            record={
                "level": types.SimpleNamespace(no=no),
                "message": text,
                "extra": {"user": "u1"},
                "exception": exception,
            }
        )

    try:
        raise ValueError("bad")
    except ValueError as error:
        caught = error
    write(message(25, "done"))
    write(message(40, "failed", types.SimpleNamespace(value=caught)))
    assert [r["level"] for r in sink.records] == ["INFO", "ERROR"]
    assert sink.records[0]["meta"] == {"user": "u1"}
    assert "ValueError: bad" in sink.records[1]["meta"]["stack"]
