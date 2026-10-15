"""The cross-language record contract: `schema/record.schema.json` and the
golden records in `schema/golden_records.json`, which logquill-js tests against
too. These tests are what keep this package's output, its parser and the
published schema saying the same thing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from logquill import (
    JSONFormatter,
    Level,
    Logger,
    LogQuillHandler,
    RunPlugin,
    TamperEvidentPlugin,
    TraceContextPlugin,
    parse_record,
)
from logquill.core.records import LEGACY_SCHEMA_VERSION, SCHEMA_VERSION, create_record
from logquill.transports.transport import CollectingTransport

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"
SCHEMA = json.loads((SCHEMA_DIR / "record.schema.json").read_text(encoding="utf-8"))
GOLDEN = json.loads((SCHEMA_DIR / "golden_records.json").read_text(encoding="utf-8"))


def _entries(group: str) -> list[Any]:
    return [pytest.param(entry, id=entry["name"]) for entry in GOLDEN[group]]


@pytest.fixture(scope="module")
def validator() -> Any:
    jsonschema = pytest.importorskip("jsonschema")
    return jsonschema.Draft202012Validator(SCHEMA, format_checker=jsonschema.FormatChecker())


def test_the_schema_is_itself_a_valid_json_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")

    jsonschema.Draft202012Validator.check_schema(SCHEMA)


def test_schema_and_fixtures_name_the_version_this_package_writes() -> None:
    assert SCHEMA["properties"]["schema_version"]["const"] == SCHEMA_VERSION
    assert GOLDEN["schema_version"] == SCHEMA_VERSION


@pytest.mark.parametrize("entry", _entries("valid"))
def test_valid_golden_records_validate_and_round_trip(
    entry: dict[str, Any], validator: Any
) -> None:
    record = entry["record"]

    validator.validate(record)

    assert json.loads(json.dumps(record)) == record
    assert parse_record(record) == record


@pytest.mark.parametrize("entry", _entries("invalid"))
def test_invalid_golden_records_are_rejected_by_the_schema(
    entry: dict[str, Any], validator: Any
) -> None:
    assert not validator.is_valid(entry["record"]), entry["why"]


@pytest.mark.parametrize("entry", _entries("legacy"))
def test_legacy_golden_records_parse_to_the_expected_shape(entry: dict[str, Any]) -> None:
    assert parse_record(entry["record"]) == entry["parsed"]


@pytest.mark.parametrize("entry", _entries("rejected"))
def test_rejected_golden_records_are_refused_by_the_parser(entry: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        parse_record(entry["record"])


def test_a_1x_record_is_labelled_1_0_and_the_input_is_left_alone() -> None:
    raw = {"timestamp": "2026-01-01T00:00:00.000Z", "level": "INFO", "logger": "a", "message": "m"}

    parsed = parse_record(raw)

    assert parsed["schema_version"] == LEGACY_SCHEMA_VERSION
    assert parsed["meta"] == {}
    assert "schema_version" not in raw and "meta" not in raw


def test_parse_errors_say_what_to_do() -> None:
    with pytest.raises(ValueError, match="upgrade logquill"):
        parse_record({"schema_version": "3.0"})
    with pytest.raises(ValueError, match="upper case"):
        parse_record({"timestamp": "t", "level": "info", "logger": "a", "message": "m", "meta": {}})
    with pytest.raises(ValueError, match="'message' must be a string"):
        parse_record({"timestamp": "t", "level": "INFO", "logger": "a", "meta": {}})


# --- what this package actually writes must satisfy the schema ---------------


def _written(logger: Logger, sink: CollectingTransport) -> list[dict[str, Any]]:
    return [json.loads(JSONFormatter().format(record)) for record in sink.records]


def test_every_record_the_logger_writes_carries_the_schema_version() -> None:
    record = Logger("app").info("hello")

    assert record is not None
    assert record["schema_version"] == SCHEMA_VERSION


def test_records_from_a_full_pipeline_validate_against_the_schema(validator: Any) -> None:
    sink = CollectingTransport()
    logger = Logger(
        "app.agent",
        level="TRACE",
        transports=[sink],
        plugins=[RunPlugin(), TraceContextPlugin(), TamperEvidentPlugin()],
    )

    logger.trace("t")
    logger.info("plain", user_id=42, nested={"a": [1, 2, {"b": None}]})
    logger.thought("hmm")
    logger.action("call", retry_count=1)
    logger.observation("got it")
    logger.decision("done")
    with logger.span("outer"), logger.span("inner"):
        logger.info("inside")
    try:
        _ = 1 / 0
    except ZeroDivisionError as exc:
        logger.error("failed", exc_info=exc)
    logger.opt(depth=0).warn("with caller")
    logger.fatal("fatal")

    written = _written(logger, sink)
    assert len(written) >= 12
    for record in written:
        validator.validate(record)
        assert parse_record(record) == record


def test_records_from_the_stdlib_bridge_validate(validator: Any) -> None:
    import logging

    sink = CollectingTransport()
    logger = Logger("bridge", transports=[sink])
    stdlib = logging.getLogger("contract-bridge-test")
    handler = LogQuillHandler(logger)
    stdlib.addHandler(handler)
    stdlib.propagate = False
    try:
        stdlib.warning("retrying", extra={"attempt": 2})
    finally:
        stdlib.removeHandler(handler)

    for record in _written(logger, sink):
        validator.validate(record)


def test_an_llm_record_from_create_record_validates(validator: Any) -> None:
    record = create_record(
        level=Level.INFO,
        logger="app",
        message="chat",
        meta={"kind": "action"},
        llm={"model": "m", "tokens_in": 10, "tokens_out": 5, "cost_usd": 0.5, "latency_ms": 9.5},
    )

    validator.validate(json.loads(JSONFormatter().format(record)))


def test_create_record_omits_llm_unless_given() -> None:
    record = create_record(level=Level.INFO, logger="a", message="m", meta={})

    assert "llm" not in record


# --- integrity: the hash chain covers the new fields, and still verifies 1.x --


def test_the_hash_chain_detects_edits_to_schema_version_and_the_llm_block() -> None:
    plugin = TamperEvidentPlugin()
    records = []
    for cost in (0.1, 0.2):
        record = create_record(
            level=Level.INFO, logger="a", message="chat", meta={}, llm={"cost_usd": cost}
        )
        plugin.before_log(record)
        records.append(record)
    assert TamperEvidentPlugin.verify_chain(records) is True

    records[1]["llm"]["cost_usd"] = 0.0  # type: ignore[typeddict-item]
    assert TamperEvidentPlugin.verify_chain(records) is False
    records[1]["llm"]["cost_usd"] = 0.2  # type: ignore[typeddict-item]
    records[1]["schema_version"] = "9.9"
    assert TamperEvidentPlugin.verify_chain(records) is False


def test_a_hash_chain_written_by_1x_still_verifies() -> None:
    plugin = TamperEvidentPlugin()
    chain = []
    for i in range(3):
        # a 1.x record: no schema_version
        record: Any = {
            "timestamp": "2026-01-01T00:00:00.000Z",
            "level": "INFO",
            "logger": "a",
            "message": f"step {i}",
            "meta": {},
        }
        plugin.before_log(record)
        chain.append(record)

    assert TamperEvidentPlugin.verify_chain(chain) is True
    assert TamperEvidentPlugin.verify_chain([parse_record(r) for r in chain]) is True

    chain[1]["message"] = "edited"
    assert TamperEvidentPlugin.verify_chain(chain) is False
