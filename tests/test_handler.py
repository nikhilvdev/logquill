from __future__ import annotations

import logging

from logquill.core.handler import LogQuillHandler
from logquill.core.logger import Logger
from logquill.transports.transport import CollectingTransport


def _make_stdlib_logger(handler: logging.Handler) -> logging.Logger:
    stdlib_logger = logging.getLogger(f"logquill-test-{id(handler)}")
    stdlib_logger.setLevel(logging.DEBUG)
    stdlib_logger.propagate = False
    stdlib_logger.addHandler(handler)
    return stdlib_logger


def test_stdlib_warning_flows_through_logquill_transport() -> None:
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], level="trace")
    handler = LogQuillHandler(logger)
    stdlib_logger = _make_stdlib_logger(handler)

    stdlib_logger.warning("retrying request")

    assert len(sink.records) == 1
    assert sink.records[0]["level"] == "WARN"
    assert sink.records[0]["message"] == "retrying request"


def test_level_mapping_matches_contract_names() -> None:
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], level="trace")
    handler = LogQuillHandler(logger)
    stdlib_logger = _make_stdlib_logger(handler)

    stdlib_logger.debug("d")
    stdlib_logger.info("i")
    stdlib_logger.warning("w")
    stdlib_logger.error("e")
    stdlib_logger.critical("c")

    levels = [r["level"] for r in sink.records]
    assert levels == ["DEBUG", "INFO", "WARN", "ERROR", "FATAL"]


def test_extra_fields_land_in_meta() -> None:
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], level="trace")
    handler = LogQuillHandler(logger)
    stdlib_logger = _make_stdlib_logger(handler)

    stdlib_logger.info("processed", extra={"user_id": 42})

    assert sink.records[0]["meta"]["user_id"] == 42


def test_exc_info_becomes_formatted_stack() -> None:
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], level="trace")
    handler = LogQuillHandler(logger)
    stdlib_logger = _make_stdlib_logger(handler)

    try:
        raise ValueError("boom")
    except ValueError:
        stdlib_logger.exception("failed")

    assert "ValueError: boom" in sink.records[0]["meta"]["stack"]


def test_logquill_level_filtering_still_applies() -> None:
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], level="error")
    handler = LogQuillHandler(logger)
    stdlib_logger = _make_stdlib_logger(handler)

    stdlib_logger.warning("dropped by logquill's own level")
    stdlib_logger.error("kept")

    assert len(sink.records) == 1
    assert sink.records[0]["message"] == "kept"
