"""LLM calls, retries, state diffs, `opt`, exceptions, `diagnose`, context and MCP propagation:
the Python-specific behaviour the pipeline spec describes (docs/spec/01-records-and-logger.md)."""

import asyncio
import json
import logging
import sys
import threading
from pathlib import Path

import pytest

import logquill
from logquill import (
    CollectingTransport,
    Logger,
    PIIRedactPlugin,
    RedactPlugin,
    RunPlugin,
    TraceContextPlugin,
    bind_context,
    current_context,
    disable,
)
from logquill.runtime import toggle
from logquill.tracing.mcp import inbound, propagate
from logquill.tracing.retry import RetryTracker


def make(**kwargs):
    sink = CollectingTransport()
    log = Logger("app", transports=[sink], async_dispatch=False, **kwargs)
    return log, sink


# ---- llm_call ---------------------------------------------------------------------------------


def test_llm_call_writes_the_first_class_llm_block_and_an_action_kind():
    log, sink = make()
    record = log.llm_call(
        model="m", tokens_in=10, tokens_out=5, cost_usd=0.5, latency_ms=12.5, finish_reason="stop", provider="p"
    )
    assert record["llm"] == {
        "model": "m",
        "tokens_in": 10,
        "tokens_out": 5,
        "cost_usd": 0.5,
        "latency_ms": 12.5,
        "finish_reason": "stop",
    }
    assert record["meta"] == {"kind": "action", "provider": "p"} and record["message"] == "llm_call"
    assert list(record) == ["schema_version", "timestamp", "level", "logger", "message", "meta", "llm"]
    assert sink.records[0]["llm"]["model"] == "m"


def test_llm_call_leaves_out_what_you_did_not_give_and_has_no_block_when_nothing_is_left():
    log, _ = make()
    assert log.llm_call(model="m")["llm"] == {"model": "m"}
    assert "llm" not in log.llm_call("nothing")


def test_a_value_that_breaks_the_contract_is_dropped_with_a_warning(caplog):
    log, _ = make()
    record = log.llm_call("x", model=5, tokens_in=-1, tokens_out=True, cost_usd="a lot", finish_reason="stop")
    assert record["llm"] == {"finish_reason": "stop"}
    assert "ignoring tokens_in=-1 — expected a non-negative integer" in caplog.text
    assert "ignoring model=5 — expected a string" in caplog.text
    assert "ignoring cost_usd='a lot' — expected a non-negative number" in caplog.text


def test_llm_call_respects_the_level_and_disable_and_opt_views_have_it_too():
    log, sink = make(level="ERROR")
    assert log.llm_call(model="m") is None
    log.set_level("INFO")
    log.opt(lazy=True).llm_call(model="m", detail=lambda: "computed")
    assert sink.records[0]["meta"]["detail"] == "computed"
    try:
        disable("app")
        assert log.llm_call(model="m") is None
    finally:
        toggle._reset()


def test_the_record_validates_against_the_published_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((Path(__file__).resolve().parents[3] / "schema" / "record.schema.json").read_text())
    log, _ = make()
    jsonschema.validate(log.llm_call(model="m", tokens_in=1, cost_usd=0.1), schema)
    jsonschema.validate(log.info("plain", a=1), schema)


# ---- retry tracking ---------------------------------------------------------------------------


def test_the_first_attempt_is_zero_and_each_reopening_counts_up():
    tracker = RetryTracker()
    assert [tracker.opened(None, "search", "1") for _ in range(3)] == [0, 1, 2]
    tracker.succeeded(None, "search", "1")
    assert tracker.opened(None, "search", "1") == 0


def test_calls_are_told_apart_by_span_tool_and_call_id():
    tracker = RetryTracker()
    assert tracker.opened("s1", "search", "a") == 0
    assert tracker.opened("s2", "search", "a") == 0, "another span"
    assert tracker.opened("s1", "calc", "a") == 0, "another tool"
    assert tracker.opened("s1", "search", "b") == 0, "another call id"
    assert tracker.opened("s1", "search", "a") == 1


def test_the_tracker_is_bounded_and_refuses_a_bad_size():
    tracker = RetryTracker(max_entries=2)
    for call in "abc":
        tracker.opened(None, "t", call)
    assert tracker.opened(None, "t", "a") == 0, "the oldest call was forgotten"
    with pytest.raises(ValueError, match="max_entries"):
        RetryTracker(max_entries=0)


def test_logger_stamps_retry_count_and_keeps_an_explicit_one():
    log, sink = make()
    log.action("a", tool="web", tool_call_id="1")
    log.action("b", tool="web", tool_call_id="1")
    log.action("c", tool="web", tool_call_id="1", retry_count=99)
    log.action("no tool")
    counts = [r["meta"].get("retry_count") for r in sink.records]
    assert counts == [None, 1, 99, None]


def test_a_successful_observation_ends_the_chain_but_one_with_an_error_does_not():
    log, sink = make()
    log.action("a", tool="web")
    log.observation("failed", tool="web", error="timeout")
    log.action("b", tool="web")
    log.observation("worked", tool="web")
    log.action("c", tool="web")
    assert [r["meta"].get("retry_count") for r in sink.records if r["meta"]["kind"] == "action"] == [None, 1, None]


def test_the_same_tool_in_different_spans_is_not_a_retry():
    log, sink = make()
    with log.span("one"):
        log.action("a", tool="web")
    with log.span("two"):
        log.action("b", tool="web")
    assert all("retry_count" not in r["meta"] for r in sink.records)


# ---- state diff -------------------------------------------------------------------------------


def span_meta(sink, name):
    return next(r for r in sink.records if r["message"] == name)["meta"]


def test_a_span_records_what_changed_in_the_captured_state():
    log, sink = make(content_policy="full")
    state = {"n": 1, "keep": "x", "nested": {"a": [1]}}
    with log.span("s", capture_state=lambda: state):
        state["n"] = 2
        state["new"] = True
        state["nested"]["a"].append(2)
        del state["keep"]
    diff = span_meta(sink, "s")["state_diff"]
    assert diff["before"] == {"n": 1, "keep": "x", "nested": {"a": [1]}}
    assert diff["after"] == {"n": 2, "new": True, "nested": {"a": [1, 2]}}


def test_no_change_a_failing_capture_and_an_explicit_diff_behave():
    log, sink = make(content_policy="full")
    with log.span("same", capture_state=lambda: {"a": 1}):
        pass
    with log.span("raises", capture_state=lambda: 1 / 0):
        pass
    with log.span("explicit", capture_state=lambda: {"a": next(counter)}, state_diff="mine"):
        pass
    with log.span("scalar", capture_state=lambda: next(counter)):
        pass
    assert "state_diff" not in span_meta(sink, "same") and "state_diff" not in span_meta(sink, "raises")
    assert span_meta(sink, "explicit")["state_diff"] == "mine"
    assert span_meta(sink, "scalar")["state_diff"] == {"before": 2, "after": 3}


counter = iter(range(100))


def test_the_state_diff_is_governed_by_the_content_policy():
    log, sink = make()  # off by default
    state = {"secret": 1}
    with log.span("s", capture_state=lambda: state):
        state["secret"] = 2
    assert span_meta(sink, "s")["state_diff"].startswith("<content capture is off")


# ---- opt: lazy values and caller depth --------------------------------------------------------


def test_lazy_values_run_only_when_the_record_will_be_emitted():
    log, sink = make(level="WARN")
    calls = []

    def expensive():
        calls.append(1)
        return "value"

    log.opt(lazy=True).debug("filtered", x=expensive)
    assert calls == []
    try:
        disable("app")
        log.opt(lazy=True).error("disabled", x=expensive)
        assert calls == []
    finally:
        toggle._reset()
    log.opt(lazy=True).error("emitted", x=expensive, plain=1, boom=lambda: 1 / 0)
    assert calls == [1]
    meta = sink.records[0]["meta"]
    assert meta["x"] == "value" and meta["plain"] == 1
    assert meta["boom"] == "<lazy value raised ZeroDivisionError: division by zero>"


def test_callables_are_left_alone_without_lazy():
    log, sink = make()
    log.info("m", f=len)
    assert sink.records[0]["meta"]["f"].startswith("<built-in function len")


def test_lazy_values_are_evaluated_on_the_calling_thread_even_when_dispatch_is_queued():
    sink = CollectingTransport()
    log = Logger("app", transports=[sink])
    seen = []
    log.opt(lazy=True).info("m", x=lambda: seen.append(threading.current_thread().name))
    log.close()
    assert seen == [threading.current_thread().name]


def test_depth_zero_reports_the_direct_caller_and_one_skips_a_wrapper():
    log, sink = make()

    def wrapper():
        log.opt(depth=1).info("from wrapper")

    log.opt(depth=0).info("direct")
    wrapper()
    direct, wrapped = (r["meta"]["caller"] for r in sink.records)
    assert direct["function"] == "test_depth_zero_reports_the_direct_caller_and_one_skips_a_wrapper"
    assert wrapped["function"] == "test_depth_zero_reports_the_direct_caller_and_one_skips_a_wrapper"
    assert direct["module"] == __name__ and direct["file"].endswith("test_tracing_features.py") and direct["line"] > 0


def test_depth_beyond_the_stack_omits_the_caller_and_an_explicit_one_wins():
    log, sink = make()
    log.opt(depth=500).info("too deep")
    log.opt(depth=0).info("explicit", caller="mine")
    assert "caller" not in sink.records[0]["meta"] and sink.records[1]["meta"]["caller"] == "mine"
    log.info("no depth asked")
    assert "caller" not in sink.records[2]["meta"]


def test_depth_works_for_every_method_family():
    log, sink = make()
    view = log.opt(depth=0)
    for call in (view.info, view.thought, view.action, view.observation, view.decision):
        call("m")
    view.llm_call(model="m")
    assert all("caller" in r["meta"] for r in sink.records) and len(sink.records) == 6


def test_a_negative_depth_is_rejected_and_views_are_cached_and_bounded():
    log, _ = make()
    with pytest.raises(ValueError, match="depth"):
        log.opt(depth=-1)
    assert log.opt(lazy=True) is log.opt(lazy=True)
    assert log.opt(lazy=True) is not log.opt(depth=1)
    views = [log.opt(depth=n) for n in range(100)]
    assert len(log._opt_cache) <= 32 and views[99] is not log.opt(depth=99)


# ---- exceptions and diagnose ------------------------------------------------------------------


def test_exc_info_accepts_true_an_instance_a_tuple_and_ignores_falsy_values():
    log, sink = make()
    try:
        raise KeyError("missing")
    except KeyError as error:
        log.error("instance", exc_info=error)
        log.error("true", exc_info=True)
        log.error("tuple", exc_info=sys.exc_info())
    log.error("outside", exc_info=True)
    log.error("falsy", exc_info=False)
    log.error("none")
    stacks = {r["message"]: r["meta"].get("stack") for r in sink.records}
    for name in ("instance", "true", "tuple"):
        assert stacks[name].startswith("Traceback") and "KeyError: 'missing'" in stacks[name]
    assert stacks["outside"] is None and stacks["falsy"] is None and stacks["none"] is None
    assert all("exc_info" not in r["meta"] for r in sink.records)


def test_a_malformed_exc_info_is_ignored_with_a_warning(caplog):
    log, sink = make()
    assert log.error("m", exc_info="nope") is not None
    assert "stack" not in sink.records[0]["meta"] and "ignoring exc_info" in caplog.text


def leaking(password="hunter2", ssn="123-45-6789", note="x" * 500):
    raise ValueError("boom")


def test_diagnose_is_off_by_default_and_shows_no_local_values():
    log, sink = make()
    try:
        leaking()
    except ValueError:
        log.error("failed", exc_info=True)
    assert "hunter2" not in sink.records[0]["meta"]["stack"]


def test_diagnose_prints_locals_warns_once_and_truncates_long_values(caplog, monkeypatch):
    # the warning is once per process, so an earlier test may already have used it up
    monkeypatch.setattr("logquill.core.exceptions._diagnose_warned", False)
    log, sink = make()
    for _ in range(2):
        try:
            leaking()
        except ValueError:
            log.error("failed", diagnose=True)
    stack = sink.records[0]["meta"]["stack"]
    assert "password = 'hunter2'" in stack or "hunter2" in stack
    assert "..." in stack and "x" * 300 not in stack, "a long repr is cut at 200 characters"
    assert caplog.text.count("diagnose=True writes local variable values") == 1


def test_diagnose_output_contains_nothing_the_redactors_would_mask():
    log, sink = make(plugins=[RedactPlugin(), PIIRedactPlugin()])
    try:
        leaking()
    except ValueError:
        log.error("failed", diagnose=True)
    stack = sink.records[0]["meta"]["stack"]
    assert "hunter2" not in stack and "123-45-6789" not in stack


def test_diagnose_covers_chained_exceptions_and_fails_closed():
    def chained():
        secret_token = "tok-chained-value"  # noqa: F841
        try:
            raise KeyError("first")
        except KeyError as error:
            raise RuntimeError("second") from error

    log, sink = make(plugins=[RedactPlugin(keys=["secret_token"])])
    try:
        chained()
    except RuntimeError:
        log.error("failed", diagnose=True)
    assert "tok-chained-value" not in sink.records[0]["meta"]["stack"]

    class Exploding(logquill.Plugin):
        def redact_local(self, name, text):
            raise RuntimeError("broken hook")

    log2, sink2 = make(plugins=[Exploding()])
    try:
        leaking()
    except ValueError:
        log2.error("failed", diagnose=True)
    stack = sink2.records[0]["meta"]["stack"]
    assert "<redaction failed>" in stack and "hunter2" not in stack


def test_a_local_whose_repr_raises_falls_back_to_a_plain_traceback():
    class Unprintable:
        def __repr__(self):
            raise RuntimeError("no repr")

    def fails():
        thing = Unprintable()  # noqa: F841
        raise ValueError("boom")

    log, sink = make()
    try:
        fails()
    except ValueError:
        log.error("failed", diagnose=True)
    assert "ValueError: boom" in sink.records[0]["meta"]["stack"]


# ---- context ----------------------------------------------------------------------------------


def test_bound_context_appears_inner_wins_and_call_site_wins_over_both():
    log, sink = make()
    with bind_context(a=1, b="outer"):
        with bind_context(b="inner", c=3):
            log.info("nested", c="call")
        log.info("outer")
    log.info("outside")
    assert sink.records[0]["meta"] == {"a": 1, "b": "inner", "c": "call"}
    assert sink.records[1]["meta"] == {"a": 1, "b": "outer"}
    assert sink.records[2]["meta"] == {} and current_context() == {}


def test_context_is_isolated_per_thread_and_per_asyncio_task():
    log, sink = make()
    seen = {}

    def worker(name):
        with bind_context(who=name):
            log.info("in thread", name=name)
            seen[name] = current_context()

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("t1", "t2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen == {"t1": {"who": "t1"}, "t2": {"who": "t2"}} and current_context() == {}

    async def task(name):
        with bind_context(who=name):
            await asyncio.sleep(0.01)
            return current_context()["who"]

    async def main():
        return await asyncio.gather(task("a"), task("b"), task("c"))

    assert asyncio.run(main()) == ["a", "b", "c"]


def test_fixed_meta_on_a_child_yields_to_bound_context_and_call_site():
    log, sink = make()
    child = log.child("db", tier="storage", region="eu")
    with bind_context(region="us"):
        child.info("m", extra=1)
    assert sink.records[0]["meta"] == {"region": "us", "tier": "storage", "extra": 1}


# ---- MCP --------------------------------------------------------------------------------------


def test_propagate_carries_a_w3c_traceparent_and_a_run_id_only_when_given():
    meta = propagate()
    assert set(meta) == {"logquill/traceparent"}
    version, trace_id, span_id, flags = meta["logquill/traceparent"].split("-")
    assert (version, len(trace_id), len(span_id), flags) == ("00", 32, 16, "01")
    assert propagate(trace_id="a" * 32)["logquill/traceparent"].split("-")[1] == "a" * 32
    assert propagate(run_id="r1")["logquill/run-id"] == "r1"


def test_a_client_and_server_share_one_trace_id_and_the_server_records_mcp_fields():
    client, client_sink = make(plugins=[TraceContextPlugin()])
    server, server_sink = make(plugins=[TraceContextPlugin(), RunPlugin(run_id="server-run")])
    request_meta = propagate(trace_id="b" * 32, run_id="client-run")
    with inbound(request_meta, server="tools", tool="search"):
        server.info("handling")
    server.info("after")
    handled, after = server_sink.records
    assert handled["meta"]["trace_id"] == "b" * 32
    assert handled["meta"]["mcp"] == {"server": "tools", "tool": "search", "run_id": "client-run"}
    assert handled["meta"]["run_id"] == "server-run", "the client's run id never shadows the server's"
    assert "mcp" not in after["meta"] and after["meta"]["trace_id"] != "b" * 32


def test_inbound_copes_with_no_meta_a_malformed_traceparent_and_an_exception():
    log, sink = make(plugins=[TraceContextPlugin()])
    with inbound(None, server="s", tool="t"):
        log.info("no meta")
    with inbound({"logquill/traceparent": "garbage"}, server="s", tool="t"):
        log.info("malformed")
    with pytest.raises(RuntimeError):
        with inbound(propagate(trace_id="c" * 32), server="s", tool="t"):
            raise RuntimeError("tool failed")
    log.info("after the failure")
    first, second, last = sink.records
    assert first["meta"]["mcp"] == {"server": "s", "tool": "t"} and len(first["meta"]["trace_id"]) == 32
    assert len(second["meta"]["trace_id"]) == 32 and second["meta"]["trace_id"] != "garbage"
    assert "mcp" not in last["meta"] and last["meta"]["trace_id"] != "c" * 32


# ---- lazy imports -----------------------------------------------------------------------------


def test_every_exported_name_resolves_and_unknown_names_still_raise():
    for name in logquill.__all__:
        assert getattr(logquill, name) is not None
    assert set(logquill.__all__) <= set(dir(logquill))
    with pytest.raises(AttributeError):
        logquill.definitely_not_a_name


def test_importing_logquill_does_not_load_heavy_modules():
    import subprocess

    code = (
        "import sys, logquill; heavy = ['asyncio','urllib.request','ssl','http.client','smtplib',"
        "'sqlite3','email','socket']; print(sorted(m for m in heavy if m in sys.modules))"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert done.stdout.strip() == "[]", done.stdout + done.stderr
