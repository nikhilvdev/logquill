from __future__ import annotations

import logging
from typing import Any

import pytest

from logquill.core.logger import Logger
from logquill.transports.cloud.cloudwatch_transport import CloudWatchTransport


class FakeCloudWatchClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list[dict[str, Any]]]] = []

    def put_log_events(
        self,
        logGroupName: str,
        logStreamName: str,
        logEvents: list[dict[str, Any]],  # noqa: N803
    ) -> None:
        self.calls.append((logGroupName, logStreamName, logEvents))


def test_puts_events_sorted_by_timestamp() -> None:
    client = FakeCloudWatchClient()
    transport = CloudWatchTransport(
        log_group="my-group", log_stream="my-stream", client=client, max_records=2
    )
    logger = Logger("app.test", transports=[transport])

    logger.info("first")
    logger.info("second")

    assert len(client.calls) == 1
    group, stream, events = client.calls[0]
    assert group == "my-group"
    assert stream == "my-stream"
    assert len(events) == 2
    assert events[0]["timestamp"] <= events[1]["timestamp"]


def test_missing_dependency_logs_actionable_install_hint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = CloudWatchTransport(log_group="g", log_stream="s", max_records=1)
    logger = Logger("app.test", transports=[transport])

    with caplog.at_level(logging.ERROR, logger="logquill"):
        logger.info("hello")  # must not raise

    assert "pip install logquill[aws]" in caplog.text
