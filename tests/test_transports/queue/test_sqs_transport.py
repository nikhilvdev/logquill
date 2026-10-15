from __future__ import annotations

import logging

import pytest

from logquill.core.logger import Logger
from logquill.transports.queue.sqs_transport import SQSTransport


class FakeSQSClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    def send_message_batch(self, QueueUrl: str, Entries: list[dict[str, str]]) -> None:  # noqa: N803
        self.calls.append((QueueUrl, Entries))


def test_chunks_at_ten_messages_per_send_message_batch_call() -> None:
    client = FakeSQSClient()
    transport = SQSTransport(
        topic="https://sqs.us-east-1.amazonaws.com/123/my-queue",
        client=client,
        max_records=25,
    )
    logger = Logger("app.test", transports=[transport])

    for i in range(25):
        logger.info(f"message {i}")

    expected_url = "https://sqs.us-east-1.amazonaws.com/123/my-queue"
    assert len(client.calls) == 3
    assert [len(entries) for _, entries in client.calls] == [10, 10, 5]
    assert all(queue_url == expected_url for queue_url, _ in client.calls)


def test_entry_ids_reset_per_chunk() -> None:
    client = FakeSQSClient()
    transport = SQSTransport(topic="q", client=client, max_records=15)
    logger = Logger("app.test", transports=[transport])

    for i in range(15):
        logger.info(f"message {i}")

    first_chunk_ids = [entry["Id"] for entry in client.calls[0][1]]
    second_chunk_ids = [entry["Id"] for entry in client.calls[1][1]]
    assert first_chunk_ids == [str(i) for i in range(10)]
    assert second_chunk_ids == [str(i) for i in range(5)]


def test_missing_dependency_logs_actionable_install_hint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = SQSTransport(topic="q", max_records=1)
    logger = Logger("app.test", transports=[transport])

    with caplog.at_level(logging.ERROR, logger="logquill"):
        logger.info("hello")  # must not raise

    assert "pip install logquill[aws]" in caplog.text
