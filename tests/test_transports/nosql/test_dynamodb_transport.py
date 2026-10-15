from __future__ import annotations

import logging
from typing import Any

import pytest

from logquill.core.logger import Logger
from logquill.transports.nosql.dynamodb_transport import DynamoDBTransport


class FakeDynamoTable:
    def __init__(self) -> None:
        self.put_items: list[dict[str, Any]] = []
        self.entered = 0
        self.exited = 0

    def batch_writer(self) -> FakeDynamoTable:
        return self

    def __enter__(self) -> FakeDynamoTable:
        self.entered += 1
        return self

    def __exit__(self, *args: object) -> bool:
        self.exited += 1
        return False

    def put_item(self, Item: dict[str, Any]) -> None:  # noqa: N803
        self.put_items.append(Item)


def test_partition_key_prefers_run_id_over_trace_id_over_logger() -> None:
    table = FakeDynamoTable()
    transport = DynamoDBTransport(table=table, max_records=3)
    logger = Logger("app.test", transports=[transport])

    logger.info("has run_id", run_id="run-1", trace_id="trace-1")
    logger.info("has trace_id only", trace_id="trace-2")
    logger.info("has neither")

    assert table.put_items[0]["run_id"] == "run-1"
    assert table.put_items[1]["run_id"] == "trace-2"
    assert table.put_items[2]["run_id"] == "app.test"


def test_sort_key_is_timestamp_and_batch_writer_used_once_per_flush() -> None:
    table = FakeDynamoTable()
    transport = DynamoDBTransport(table=table, max_records=2)
    logger = Logger("app.test", transports=[transport])

    logger.info("first")
    logger.info("second")

    assert table.entered == 1
    assert table.exited == 1
    assert all("timestamp" in item for item in table.put_items)


def test_missing_dependency_logs_actionable_install_hint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = DynamoDBTransport(max_records=1)
    logger = Logger("app.test", transports=[transport])

    with caplog.at_level(logging.ERROR, logger="logquill"):
        logger.info("hello")  # must not raise

    assert "pip install logquill[aws]" in caplog.text
