import pytest

from logquill.core.logger import Logger
from logquill.transports.transport import CollectingTransport


def test_child_namespaces_under_parent() -> None:
    logger = Logger("app")
    child = logger.child("agent")

    assert child.name == "app.agent"


def test_child_shares_parent_transports() -> None:
    sink = CollectingTransport()
    logger = Logger("app", transports=[sink])
    child = logger.child("agent")

    child.info("from child")

    assert len(sink.records) == 1
    assert sink.records[0]["logger"] == "app.agent"


def test_child_fixed_meta_named_name_does_not_collide_with_the_positional_arg() -> None:
    logger = Logger("app")

    child = logger.child("agent", name="not the real name")

    assert child.name == "app.agent"


def test_span_kwarg_named_name_does_not_collide_with_the_positional_arg() -> None:
    sink = CollectingTransport()
    logger = Logger("app", transports=[sink])

    with logger.span("call_llm", name="not the real name"):
        pass

    assert sink.records[0]["message"] == "call_llm"
    assert sink.records[0]["meta"]["name"] == "not the real name"


def test_child_fixed_meta_is_injected_into_every_record() -> None:
    logger = Logger("app")
    child = logger.child("agent", run="nightly")

    record = child.info("hello", step=1)

    assert record is not None
    assert record["meta"] == {"run": "nightly", "step": 1}


def test_child_plugins_do_not_affect_parent() -> None:
    sink = CollectingTransport()
    logger = Logger("app", transports=[sink])
    child = logger.child("agent")
    child.use(lambda record: None)  # drop everything logged on the child

    assert child.info("dropped") is None
    assert logger.info("kept") is not None


@pytest.mark.parametrize(
    ("method", "kind"),
    [
        ("thought", "thought"),
        ("action", "action"),
        ("observation", "observation"),
        ("decision", "decision"),
    ],
)
def test_agentic_convenience_methods_stamp_kind(method: str, kind: str) -> None:
    logger = Logger("app")
    record = getattr(logger, method)("step", extra=1)

    assert record is not None
    assert record["meta"]["kind"] == kind
    assert record["meta"]["extra"] == 1


def test_agentic_convenience_method_call_site_can_override_kind() -> None:
    logger = Logger("app")
    record = logger.thought("step", kind="custom")

    assert record is not None
    assert record["meta"]["kind"] == "custom"


def test_span_emits_a_record_with_span_id_and_duration_ms() -> None:
    logger = Logger("app")

    with logger.span("call_llm") as span:
        pass

    sink_id = span._span_id
    assert isinstance(sink_id, str) and sink_id


def test_span_close_record_has_no_parent_span_id_at_top_level() -> None:
    sink = CollectingTransport()
    logger = Logger("app", transports=[sink])

    with logger.span("call_llm"):
        pass

    assert len(sink.records) == 1
    record = sink.records[0]
    assert "span_id" in record["meta"]
    assert isinstance(record["meta"]["duration_ms"], float)
    assert "parent_span_id" not in record["meta"]
    assert record["meta"]["kind"] == "span"


def test_records_inside_a_span_are_stamped_with_parent_span_id() -> None:
    sink = CollectingTransport()
    logger = Logger("app", transports=[sink])

    with logger.span("call_llm") as span:
        logger.action("call tool")

    action_record, span_record = sink.records
    assert action_record["message"] == "call tool"
    assert action_record["meta"]["parent_span_id"] == span._span_id
    assert span_record["meta"]["span_id"] == span._span_id


def test_nested_spans_form_a_parent_chain() -> None:
    sink = CollectingTransport()
    logger = Logger("app", transports=[sink])

    with logger.span("outer") as outer, logger.span("inner") as inner:
        logger.action("leaf")

    leaf, inner_record, outer_record = sink.records
    assert leaf["meta"]["parent_span_id"] == inner._span_id
    assert inner_record["meta"]["span_id"] == inner._span_id
    assert inner_record["meta"]["parent_span_id"] == outer._span_id
    assert outer_record["meta"]["span_id"] == outer._span_id
    assert "parent_span_id" not in outer_record["meta"]


def test_span_still_emits_on_exception_and_reraises() -> None:
    sink = CollectingTransport()
    logger = Logger("app", transports=[sink])

    with pytest.raises(ValueError, match="boom"), logger.span("risky"):
        raise ValueError("boom")

    assert len(sink.records) == 1
    record = sink.records[0]
    assert record["level"] == "ERROR"
    assert "ValueError: boom" in record["meta"]["error"]


def test_span_accepts_explicit_span_id_and_parent_span_id() -> None:
    sink = CollectingTransport()
    logger = Logger("app", transports=[sink])

    with logger.span("chain", span_id="abc123", parent_span_id="parent456"):
        pass

    record = sink.records[0]
    assert record["meta"]["span_id"] == "abc123"
    assert record["meta"]["parent_span_id"] == "parent456"


def test_span_kwargs_become_meta() -> None:
    sink = CollectingTransport()
    logger = Logger("app", transports=[sink])

    with logger.span("chain", model="gpt-4"):
        pass

    assert sink.records[0]["meta"]["model"] == "gpt-4"
