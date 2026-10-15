from __future__ import annotations

import logging

import pytest

from logquill.core.logger import Logger
from logquill.transports.queue.pubsub_transport import PubSubTransport


class FakeFuture:
    def __init__(self) -> None:
        self.waited = False

    def result(self) -> str:
        self.waited = True
        return "message-id"


class FakePubSubTopic:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []
        self.futures: list[FakeFuture] = []

    def publish(self, topic: str, data: bytes) -> FakeFuture:
        self.published.append((topic, data))
        future = FakeFuture()
        self.futures.append(future)
        return future


def test_publishes_and_waits_on_every_future() -> None:
    topic_path = "projects/my-project/topics/app-logs"
    client = FakePubSubTopic()
    transport = PubSubTransport(topic=topic_path, client=client, max_records=2)
    logger = Logger("app.test", transports=[transport])

    logger.info("first")
    logger.info("second")

    assert len(client.published) == 2
    assert all(topic == topic_path for topic, _ in client.published)
    assert all(future.waited for future in client.futures)


def test_missing_dependency_logs_actionable_install_hint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = PubSubTransport(topic="projects/p/topics/t", max_records=1)
    logger = Logger("app.test", transports=[transport])

    with caplog.at_level(logging.ERROR, logger="logquill"):
        logger.info("hello")  # must not raise

    assert "pip install logquill[pubsub]" in caplog.text
