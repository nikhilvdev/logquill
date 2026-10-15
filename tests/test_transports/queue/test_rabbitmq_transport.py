from __future__ import annotations

import logging

import pytest

from logquill.core.logger import Logger
from logquill.transports.queue.rabbitmq_transport import RabbitMQTransport


class FakeChannel:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, bytes]] = []

    def basic_publish(self, exchange: str, routing_key: str, body: bytes) -> None:
        self.published.append((exchange, routing_key, body))


def test_publishes_one_message_per_record_to_the_default_exchange() -> None:
    channel = FakeChannel()
    transport = RabbitMQTransport(topic="app-logs", channel=channel, max_records=2)
    logger = Logger("app.test", transports=[transport])

    logger.info("first")
    logger.info("second")

    assert len(channel.published) == 2
    assert all(exchange == "" for exchange, _, _ in channel.published)
    assert all(routing_key == "app-logs" for _, routing_key, _ in channel.published)


def test_missing_dependency_logs_actionable_install_hint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = RabbitMQTransport(topic="app-logs", max_records=1)
    logger = Logger("app.test", transports=[transport])

    with caplog.at_level(logging.ERROR, logger="logquill"):
        logger.info("hello")  # must not raise

    assert "pip install logquill[rabbitmq]" in caplog.text
