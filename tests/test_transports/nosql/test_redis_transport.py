from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from logquill.core.logger import Logger
from logquill.transports.nosql.redis_transport import RedisTransport


class FakeRedisClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def xadd(self, name: str, fields: dict[str, Any]) -> None:
        self.calls.append((name, fields))


def test_one_xadd_call_per_record_in_the_batch() -> None:
    client = FakeRedisClient()
    transport = RedisTransport(client=client, stream="my-logs", max_records=2)
    logger = Logger("app.test", transports=[transport])

    logger.info("first")
    logger.info("second")

    assert len(client.calls) == 2
    assert all(name == "my-logs" for name, _ in client.calls)
    assert json.loads(client.calls[0][1]["meta"]) == {}


def test_missing_dependency_logs_actionable_install_hint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = RedisTransport(max_records=1)
    logger = Logger("app.test", transports=[transport])

    with caplog.at_level(logging.ERROR, logger="logquill"):
        logger.info("hello")  # must not raise

    assert "pip install logquill[redis]" in caplog.text
