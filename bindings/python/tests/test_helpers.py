"""The pure helpers, checked against the project's shared golden fixtures, and the converter against
hostile input. The golden files are the same ones the Rust core and the JavaScript package run."""

import json
import math
from pathlib import Path

import pytest

import logquill._native as logquill

SCHEMA = Path(__file__).resolve().parents[3] / "schema"
GOLDEN = json.loads((SCHEMA / "golden_records.json").read_text(encoding="utf-8"))
CHAIN = json.loads((SCHEMA / "golden_chain.json").read_text(encoding="utf-8"))


def ids(cases):
    return [case["name"] for case in cases]


# ---- shared golden records ----------------------------------------------------------------


@pytest.mark.parametrize("case", GOLDEN["valid"], ids=ids(GOLDEN["valid"]))
def test_a_valid_record_is_accepted_unchanged(case):
    assert logquill.parse_record(case["record"]) == case["record"]


@pytest.mark.parametrize("case", GOLDEN["legacy"], ids=ids(GOLDEN["legacy"]))
def test_a_legacy_record_reads_as_the_documented_current_form(case):
    assert logquill.parse_record(case["record"]) == case["parsed"]


@pytest.mark.parametrize("case", GOLDEN["rejected"], ids=ids(GOLDEN["rejected"]))
def test_a_rejected_record_raises_value_error_with_a_reason(case):
    with pytest.raises(ValueError) as error:
        logquill.parse_record(case["record"])
    assert str(error.value)


def test_the_shared_hash_chain_verifies_and_reports_its_head():
    result = logquill.verify_chain(CHAIN)
    assert result["ok"] is True
    assert result["records_checked"] == len(CHAIN)
    assert result["head_hash"] == CHAIN[-1]["meta"]["hash"]


def test_an_edit_a_removal_and_a_reorder_are_each_caught():
    edited = json.loads(json.dumps(CHAIN))
    edited[1]["message"] = "changed"
    assert logquill.verify_chain(edited)["ok"] is False
    removed = CHAIN[:1] + CHAIN[2:]
    result = logquill.verify_chain(removed)
    assert result["ok"] is False and result["broken_at"] == 2
    assert logquill.verify_chain([CHAIN[1], CHAIN[0]] + CHAIN[2:])["ok"] is False


def test_verify_accepts_any_iterable_and_a_custom_genesis():
    assert logquill.verify_chain(iter(CHAIN))["ok"] is True
    assert logquill.verify_chain(CHAIN, genesis="f" * 64)["ok"] is False
    assert logquill.GENESIS_HASH == "0" * 64


# ---- formatting ---------------------------------------------------------------------------

RECORD = {
    "schema_version": "2.0",
    "timestamp": "2026-01-01T00:00:00.000Z",
    "level": "INFO",
    "logger": "app",
    "message": "hello",
    "meta": {"user": "u1", "n": 3},
}


def test_the_three_formatters_agree_with_the_core():
    assert json.loads(logquill.format_json(RECORD)) == RECORD
    assert logquill.format_text(RECORD).startswith("2026-01-01T00:00:00.000Z INFO  app: hello")
    fields = logquill.parse_logfmt(logquill.format_logfmt(RECORD))
    assert fields["level"] == "INFO" and fields["message"] == "hello"


def test_a_stack_goes_on_its_own_lines():
    record = {**RECORD, "meta": {"stack": "Traceback\n  File x\nValueError: boom"}}
    lines = logquill.format_text(record).split("\n")
    assert lines[1:] == ["Traceback", "  File x", "ValueError: boom"]


def test_canonical_json_sorts_keys_escapes_non_ascii_and_uses_pythons_separators():
    # This is the text the tamper chain hashes, so its spelling is part of the contract.
    assert logquill.canonical_json({"b": 1, "a": "é"}) == '{"a": "\\u00e9", "b": 1}'


# ---- the converter treats meta as hostile --------------------------------------------------


def test_a_structure_that_contains_itself_does_not_loop():
    loop = {"name": "x"}
    loop["self"] = loop
    loop["again"] = [loop, loop]
    out = json.loads(logquill.format_json({**RECORD, "meta": loop}))
    assert out["meta"]["self"] == "<circular reference>"
    assert out["meta"]["again"] == ["<circular reference>", "<circular reference>"]


def test_a_wide_cycle_is_cut_at_once_not_after_exponential_work():
    # Two references per level would take 2**100 steps if only depth ended the walk.
    node = {}
    node["a"] = node
    node["b"] = node
    node["c"] = [node, node, node]
    assert "<circular reference>" in logquill.format_json({**RECORD, "meta": node})


def test_the_same_object_twice_is_not_a_cycle():
    shared = {"k": 1}
    out = json.loads(logquill.format_json({**RECORD, "meta": {"a": shared, "b": shared}}))
    assert out["meta"] == {"a": {"k": 1}, "b": {"k": 1}}


def test_very_deep_nesting_is_cut_off_with_a_marker():
    deep = current = {}
    for _ in range(500):
        current["next"] = {}
        current = current["next"]
    text = logquill.format_json({**RECORD, "meta": deep})
    assert "<max depth exceeded>" in text


def test_nan_and_infinity_become_null_in_logs_but_refuse_to_be_hashed():
    meta = {"a": math.nan, "b": math.inf, "c": -math.inf}
    out = json.loads(logquill.format_json({**RECORD, "meta": meta}))
    assert out["meta"] == {"a": None, "b": None, "c": None}
    with pytest.raises(ValueError, match="NaN and infinity"):
        logquill.canonical_json(meta)


def test_integers_and_floats_keep_their_python_spelling():
    meta = {"big": 2**80, "neg": -(2**70), "f": 1e16, "g": 1e-07, "h": 0.1, "z": -0.0, "t": True}
    text = logquill.format_json({**RECORD, "meta": meta})
    assert '"big":1208925819614629174706176' in text
    assert '"f":1e+16' in text and '"g":1e-07' in text and '"h":0.1' in text and '"z":-0.0' in text
    assert '"t":true' in text
    back = logquill.parse_record(json.loads(text))["meta"]
    assert back["big"] == 2**80 and back["h"] == 0.1


def test_unknown_types_are_written_with_str_and_never_raise():
    import datetime
    import uuid

    class Broken:
        def __str__(self):
            raise RuntimeError("no text for you")

    meta = {"when": datetime.date(2026, 1, 2), "id": uuid.UUID(int=1), "b": Broken(), "s": {1, 2}}
    out = json.loads(logquill.format_json({**RECORD, "meta": meta}))["meta"]
    assert out["when"] == "2026-01-02"
    assert out["id"] == "00000000-0000-0000-0000-000000000001"
    assert out["b"].startswith("<unprintable")
    assert out["s"] == "{1, 2}"


def test_dictionary_keys_follow_json_dumps_and_an_odd_key_is_an_error():
    meta = {1: "a", None: "c", 2.5: "d"}  # True would be the same key as 1 in a Python dict
    assert json.loads(logquill.format_json({**RECORD, "meta": meta}))["meta"] == {
        "1": "a",
        "null": "c",
        "2.5": "d",
    }
    assert json.loads(logquill.format_json({**RECORD, "meta": {True: "b"}}))["meta"] == {"true": "b"}
    with pytest.raises(TypeError, match="dictionary key"):
        logquill.format_json({**RECORD, "meta": {(1, 2): "tuple key"}})


def test_tuples_are_lists_and_lone_surrogates_do_not_crash():
    out = json.loads(logquill.format_json({**RECORD, "meta": {"t": (1, "a"), "s": "x\ud800y"}}))
    assert out["meta"]["t"] == [1, "a"]
    assert out["meta"]["s"].startswith("x") and out["meta"]["s"].endswith("y")


def test_a_non_mapping_record_is_refused_without_crashing():
    with pytest.raises(ValueError):
        logquill.parse_record(["not", "a", "record"])


def test_version_is_exposed():
    assert logquill.__version__.startswith("3.0.0")
