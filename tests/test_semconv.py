from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pytest

from logquill import Level
from logquill.core.records import create_record
from logquill.tracing import semconv


def _record(message: str = "m", *, llm: dict[str, Any] | None = None, **meta: Any) -> Any:
    record = create_record(
        level=Level.INFO, logger="app.agent", message=message, meta=meta, llm=llm
    )
    record["timestamp"] = "2026-01-01T00:00:10.000Z"
    return record


LATEST = semconv.resolve_convention("latest")
LEGACY = semconv.resolve_convention("legacy")


# --- the mapping ---------------------------------------------------------------


def test_an_llm_record_maps_to_a_chat_span_with_usage_attributes() -> None:
    record = _record(
        "chat",
        kind="action",
        provider="openai",
        llm={
            "model": "m1",
            "tokens_in": 12,
            "tokens_out": 5,
            "cost_usd": 0.5,
            "latency_ms": 250,
            "finish_reason": "stop",
        },
    )

    spec = semconv.llm_spec(record, LATEST)

    assert spec.name == "chat m1"
    assert spec.kind == "client"
    assert spec.duration_ms == 250
    assert spec.attributes == {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": "m1",
        "gen_ai.usage.input_tokens": 12,
        "gen_ai.usage.output_tokens": 5,
        "gen_ai.response.finish_reasons": ["stop"],
        "logquill.cost_usd": 0.5,
    }


def test_text_completion_is_chosen_by_meta_operation() -> None:
    spec = semconv.llm_spec(_record("c", operation="text_completion", llm={"model": "m1"}), LATEST)

    assert spec.name == "text_completion m1"
    assert spec.attributes["gen_ai.operation.name"] == "text_completion"


def test_an_llm_call_without_a_model_or_provider_still_has_the_required_attributes() -> None:
    spec = semconv.llm_spec(_record(llm={"tokens_in": 1}), LATEST, default_provider="acme")

    assert spec.name == "chat"
    assert spec.attributes["gen_ai.provider.name"] == "acme"
    assert "gen_ai.request.model" not in spec.attributes


def test_a_tool_action_maps_to_execute_tool() -> None:
    spec = semconv.tool_spec(
        _record(
            "s", kind="action", tool="search", tool_call_id="c1", retry_count=2, duration_ms=30
        ),
        LATEST,
    )

    assert spec.name == "execute_tool search"
    assert spec.kind == "internal"
    assert spec.duration_ms == 30
    assert spec.attributes == {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "search",
        "gen_ai.tool.call.id": "c1",
        "logquill.retry_count": 2,
    }


def test_a_span_marked_as_an_agent_run_is_invoke_agent() -> None:
    run = _record(
        "run", kind="span", span_id="a" * 16, operation="invoke_agent", thread_id="conv-1"
    )

    spec = semconv.span_spec(run, LATEST)

    assert spec.name == "invoke_agent app.agent"
    assert spec.kind == "client"
    assert spec.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert spec.attributes["gen_ai.conversation.id"] == "conv-1"


def test_an_ordinary_span_keeps_its_name_even_when_it_is_the_outermost_of_a_run() -> None:
    root = _record("run", kind="span", span_id="a" * 16, run_id="run-1")
    nested = _record("step", kind="span", span_id="b" * 16, run_id="run-1", parent_span_id="a" * 16)

    for record, name in ((root, "run"), (nested, "step")):
        spec = semconv.span_spec(record, LATEST)
        assert spec.name == name
        assert spec.kind == "internal"
        assert "gen_ai.operation.name" not in spec.attributes


def test_naming_an_agent_makes_a_span_an_agent_run() -> None:
    record = _record(
        "go", kind="span", span_id="a" * 16, operation="invoke_agent", agent_name="planner"
    )

    spec = semconv.span_spec(record, LATEST)

    assert spec.name == "invoke_agent planner"
    assert spec.attributes["gen_ai.agent.name"] == "planner"


def test_error_spans_carry_error_type() -> None:
    spec = semconv.tool_spec(
        _record("s", kind="action", tool="t", error="TimeoutError: too slow"), LATEST
    )

    assert spec.is_error is True
    assert spec.attributes["error.type"] == "TimeoutError"


def test_state_diff_becomes_a_span_event() -> None:
    spec = semconv.span_spec(
        _record(
            "s", kind="span", span_id="a" * 16, state_diff={"before": {"n": 1}, "after": {"n": 2}}
        ),
        LATEST,
    )

    ((name, attributes),) = spec.events
    assert name == "logquill.state_diff"
    assert attributes == {"logquill.state": '{"before":{"n":1},"after":{"n":2}}'}


def test_message_content_is_only_included_when_captured() -> None:
    messages = [{"role": "user", "parts": [{"type": "text", "content": "hi"}]}]
    record = _record("c", input_messages=messages, output_messages=[], llm={"model": "m"})

    off = semconv.llm_spec(record, LATEST)
    on = semconv.llm_spec(record, LATEST, capture_content=True)

    assert "gen_ai.input.messages" not in off.attributes
    assert (
        on.attributes["gen_ai.input.messages"]
        == '[{"role":"user","parts":[{"type":"text","content":"hi"}]}]'
    )
    assert on.attributes["gen_ai.output.messages"] == "[]"


def test_only_the_named_record_kinds_are_recognized() -> None:
    assert not semconv.is_span_record(_record("plain"))
    assert not semconv.is_tool_record(_record("a", kind="action"))  # no tool named
    assert not semconv.is_tool_record(_record("a", kind="observation", tool="t"))
    assert not semconv.is_llm_record(_record("plain"))


def test_log_attributes_for_an_llm_record() -> None:
    record = _record(llm={"model": "m", "tokens_in": 3, "tokens_out": 4, "finish_reason": "length"})

    assert semconv.log_attributes(record, LATEST) == {
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "m",
        "gen_ai.usage.input_tokens": 3,
        "gen_ai.usage.output_tokens": 4,
        "gen_ai.response.finish_reasons": ["length"],
    }
    assert semconv.log_attributes(_record("plain"), LATEST) == {}


def test_epoch_ns_reads_the_record_timestamp() -> None:
    assert semconv.epoch_ns("1970-01-01T00:00:01.500Z") == 1_500_000_000


# --- choosing the naming generation ---------------------------------------------


def test_the_legacy_generation_uses_the_older_names_for_provider_and_tokens() -> None:
    spec = semconv.llm_spec(
        _record(provider="openai", llm={"model": "m", "tokens_in": 1, "tokens_out": 2}), LEGACY
    )

    assert spec.attributes["gen_ai.system"] == "openai"
    assert spec.attributes["gen_ai.usage.prompt_tokens"] == 1
    assert spec.attributes["gen_ai.usage.completion_tokens"] == 2
    assert "gen_ai.provider.name" not in spec.attributes
    assert "gen_ai.usage.input_tokens" not in spec.attributes


def test_explicit_choice_wins_and_unknown_choice_lists_the_valid_ones() -> None:
    assert (
        semconv.resolve_convention(
            "legacy", {"OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental"}
        )
        is LEGACY
    )
    with pytest.raises(ValueError, match="latest, legacy"):
        semconv.resolve_convention("v9")


def test_the_environment_opt_in_selects_the_latest_names() -> None:
    env = {"OTEL_SEMCONV_STABILITY_OPT_IN": "http, gen_ai_latest_experimental"}

    assert semconv.resolve_convention(None, env) is LATEST
    assert semconv.resolve_convention(None, {}) is semconv._DEFAULT


def test_a_dual_emission_request_is_refused_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    semconv._warned_opt_in.clear()

    with caplog.at_level(logging.WARNING, logger="logquill"):
        convention = semconv.resolve_convention(
            None, {"OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai/dup"}
        )
        semconv.resolve_convention(None, {"OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai/dup"})

    assert convention is semconv._DEFAULT
    assert len([r for r in caplog.records if "never emits old and new" in r.getMessage()]) == 1


@pytest.mark.parametrize(
    "value, expected", [("true", True), ("TRUE", True), ("false", False), ("", False)]
)
def test_content_capture_follows_the_environment(value: str, expected: bool) -> None:
    assert semconv.capture_content_enabled({semconv.CAPTURE_CONTENT_ENV: value}) is expected
    assert semconv.capture_content_enabled({}) is False


# --- the convention strings live in exactly one file ------------------------------


def test_no_genai_attribute_name_appears_outside_the_mapping_module() -> None:
    package = Path(__file__).resolve().parent.parent / "logquill"
    pattern = re.compile(r"gen_ai[._]|\bgen_ai\b")
    offenders = [
        f"{path.relative_to(package)}:{number}"
        for path in package.rglob("*.py")
        if path.name != "semconv.py"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if pattern.search(line)
    ]

    assert offenders == []


def test_moving_to_a_newer_convention_is_an_edit_to_the_mapping_module_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renamed = semconv.Convention(
        version="9.9.9",
        names={**semconv._LATEST.names, "input_tokens": "gen_ai.usage.renamed_input_tokens"},
    )
    monkeypatch.setitem(semconv._CONVENTIONS, "latest", renamed)

    spec = semconv.llm_spec(
        _record(llm={"model": "m", "tokens_in": 4}), semconv.resolve_convention("latest")
    )

    assert spec.attributes["gen_ai.usage.renamed_input_tokens"] == 4
