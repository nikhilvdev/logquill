from __future__ import annotations

import pytest

from logquill import Logger
from logquill.privacy.privacy import (
    CONTENT_FIELDS,
    FIELD_CLASSES,
    apply_content_policy,
    keys_in_class,
    parse_content_policy,
)
from logquill.transports.transport import CollectingTransport


def test_keys_in_class_returns_every_key_tagged_with_that_class() -> None:
    secrets = keys_in_class("secret")

    assert secrets == {"password", "token", "secret", "api_key", "authorization"}
    assert all(FIELD_CLASSES[key] == "secret" for key in secrets)


def test_content_fields_matches_the_registrys_content_class() -> None:
    assert set(CONTENT_FIELDS) == keys_in_class("content")
    assert "input_messages" in CONTENT_FIELDS
    assert "state_diff" in CONTENT_FIELDS


def test_parse_content_policy_accepts_the_four_valid_choices() -> None:
    for policy in ("off", "hash", "truncate", "full"):
        assert parse_content_policy(policy) == policy


def test_parse_content_policy_rejects_anything_else() -> None:
    with pytest.raises(ValueError, match="off.*hash.*truncate.*full"):
        parse_content_policy("plaintext")


# --- apply_content_policy ---------------------------------------------------


def test_full_policy_is_a_true_no_op() -> None:
    meta = {"input_messages": ["raw"], "other": 1}

    apply_content_policy(meta, "full")

    assert meta == {"input_messages": ["raw"], "other": 1}


def test_off_policy_replaces_content_fields_with_a_placeholder() -> None:
    meta = {"input_messages": ["raw prompt"], "other": 1}

    apply_content_policy(meta, "off")

    assert "raw prompt" not in str(meta["input_messages"])
    assert "content capture is off" in meta["input_messages"]
    assert meta["other"] == 1


def test_off_policy_only_touches_classified_content_fields() -> None:
    meta = {"password": "hunter2", "note": "whatever"}

    apply_content_policy(meta, "off")

    assert meta == {"password": "hunter2", "note": "whatever"}


def test_hash_policy_is_stable_and_never_shows_the_raw_value() -> None:
    meta_a = {"input_messages": [{"role": "user", "content": "secret prompt"}]}
    meta_b = {"input_messages": [{"role": "user", "content": "secret prompt"}]}

    apply_content_policy(meta_a, "hash")
    apply_content_policy(meta_b, "hash")

    assert meta_a["input_messages"].startswith("sha256:")
    assert "secret prompt" not in meta_a["input_messages"]
    assert meta_a["input_messages"] == meta_b["input_messages"]  # same content, same hash


def test_hash_policy_differs_for_different_content() -> None:
    meta_a = {"input_messages": "one"}
    meta_b = {"input_messages": "two"}

    apply_content_policy(meta_a, "hash")
    apply_content_policy(meta_b, "hash")

    assert meta_a["input_messages"] != meta_b["input_messages"]


def test_truncate_policy_keeps_short_values_whole() -> None:
    meta = {"tool_arguments": {"q": "short"}}

    apply_content_policy(meta, "truncate")

    assert meta["tool_arguments"] == '{"q": "short"}' or "short" in meta["tool_arguments"]


def test_truncate_policy_cuts_long_values_with_a_marker() -> None:
    meta = {"output_messages": "x" * 5000}

    apply_content_policy(meta, "truncate")

    assert len(meta["output_messages"]) < 5000
    assert "truncated" in meta["output_messages"]
    assert "5002" in meta["output_messages"] or "5000" in meta["output_messages"]


def test_a_circular_reference_falls_back_to_str_instead_of_raising() -> None:
    cyclic: dict = {"a": 1}
    cyclic["self"] = cyclic
    meta = {"state_diff": cyclic}

    apply_content_policy(meta, "hash")  # must not raise

    assert meta["state_diff"].startswith("sha256:")


def test_apply_content_policy_accepts_a_custom_field_list() -> None:
    meta = {"my_custom_field": "secret-ish", "input_messages": "untouched"}

    apply_content_policy(meta, "off", fields=("my_custom_field",))

    assert "content capture is off" in meta["my_custom_field"]
    assert meta["input_messages"] == "untouched"  # not in the custom field list


# --- integration through Logger ---------------------------------------------


def test_the_default_content_policy_is_off() -> None:
    assert Logger("app").content_policy == "off"


def test_a_seeded_secret_inside_a_prompt_never_reaches_any_transport_by_default() -> None:
    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])

    logger.llm_call(
        "chat",
        model="m",
        input_messages=[
            {
                "role": "user",
                "parts": [{"type": "text", "content": "sk-ant-leaked-secret-key-material-here"}],
            }
        ],
    )

    assert "sk-ant-leaked-secret-key-material-here" not in str(sink.records[0])


def test_content_policy_full_passes_content_through() -> None:
    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink], content_policy="full")

    logger.llm_call("chat", model="m", input_messages=["raw prompt"])

    assert sink.records[0]["meta"]["input_messages"] == ["raw prompt"]


def test_content_policy_applies_before_any_plugin_sees_the_record() -> None:
    from logquill.plugins.plugin import Plugin

    seen = {}

    class Spy(Plugin):
        def before_log(self, record):  # type: ignore[no-untyped-def]
            seen["input_messages"] = record["meta"].get("input_messages")
            return record

    logger = Logger("app.agent", plugins=[Spy()])  # default content_policy="off"

    logger.llm_call("chat", model="m", input_messages=["raw prompt"])

    assert seen["input_messages"] != ["raw prompt"]
    assert "content capture is off" in seen["input_messages"]


def test_content_policy_covers_span_captured_state_too() -> None:
    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink])
    state = {"sensitive": "secret-value"}

    with logger.span("work", capture_state=lambda: state):
        state["sensitive"] = "changed"

    assert "secret-value" not in str(sink.records[0])
    assert "changed" not in str(sink.records[0])


def test_child_loggers_inherit_the_parent_content_policy() -> None:
    parent = Logger("app", content_policy="hash")

    child = parent.child("sub")

    assert child.content_policy == "hash"


def test_an_invalid_content_policy_raises_at_construction() -> None:
    with pytest.raises(ValueError, match="content_policy"):
        Logger("app", content_policy="plaintext")
