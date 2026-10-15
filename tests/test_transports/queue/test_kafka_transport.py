from __future__ import annotations

import json
import logging

import pytest

from logquill.core.logger import Logger
from logquill.transports.queue.kafka_transport import KafkaTransport


class FakeKafkaProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes, bytes | None]] = []
        self.flush_count = 0

    def send(self, topic: str, value: bytes, key: bytes | None = None) -> None:
        self.sent.append((topic, value, key))

    def flush(self) -> None:
        self.flush_count += 1


def test_publishes_batch_keyed_by_run_id() -> None:
    producer = FakeKafkaProducer()
    transport = KafkaTransport(topic="app-logs", producer=producer, max_records=2)
    logger = Logger("app.test", transports=[transport])

    logger.info("first", run_id="run-1")
    logger.info("second", run_id="run-1")

    assert len(producer.sent) == 2
    assert all(topic == "app-logs" for topic, _, _ in producer.sent)
    assert producer.sent[0][2] == b"run-1"
    assert producer.flush_count == 1


def test_falls_back_to_trace_id_then_no_key() -> None:
    producer = FakeKafkaProducer()
    transport = KafkaTransport(topic="app-logs", producer=producer, max_records=1)
    logger = Logger("app.test", transports=[transport])

    logger.info("traced only", trace_id="trace-9")
    logger.info("untraced")

    assert producer.sent[0][2] == b"trace-9"
    assert producer.sent[1][2] is None
    payload = json.loads(producer.sent[1][1])
    assert payload["message"] == "untraced"


def test_missing_dependency_logs_actionable_install_hint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = KafkaTransport(topic="app-logs", max_records=1)
    logger = Logger("app.test", transports=[transport])

    with caplog.at_level(logging.ERROR, logger="logquill"):
        logger.info("hello")  # must not raise

    assert "pip install logquill[kafka]" in caplog.text
