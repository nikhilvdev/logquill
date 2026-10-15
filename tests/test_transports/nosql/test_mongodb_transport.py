from __future__ import annotations

import logging
from typing import Any, Sequence

import pytest

from logquill.core.logger import Logger
from logquill.transports.nosql.mongodb_transport import MongoDBTransport


class FakeCollection:
    def __init__(self) -> None:
        self.batches: list[list[dict[str, Any]]] = []

    def insert_many(self, documents: Sequence[dict[str, Any]]) -> None:
        self.batches.append(list(documents))


def test_batches_records_as_documents_one_to_one() -> None:
    collection = FakeCollection()
    transport = MongoDBTransport(collection=collection, max_records=2)
    logger = Logger("app.test", transports=[transport])

    logger.info("first")
    logger.info("second")

    assert len(collection.batches) == 1
    assert len(collection.batches[0]) == 2
    assert collection.batches[0][0]["message"] == "first"


def test_missing_dependency_logs_actionable_install_hint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = MongoDBTransport(max_records=1)
    logger = Logger("app.test", transports=[transport])

    with caplog.at_level(logging.ERROR, logger="logquill"):
        logger.info("hello")  # must not raise

    assert "pip install logquill[mongodb]" in caplog.text
