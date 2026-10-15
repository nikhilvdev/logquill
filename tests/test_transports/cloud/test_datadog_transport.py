from __future__ import annotations

import logging
from typing import Sequence

import pytest

from logquill.core.logger import Logger
from logquill.transports.cloud.datadog_transport import DatadogTransport


class FakeSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Sequence[str]]] = []

    def __call__(self, url: str, api_key: str, batch: Sequence[str]) -> None:
        self.calls.append((url, api_key, batch))


def test_url_is_region_specific_and_batch_is_sent() -> None:
    sender = FakeSender()
    transport = DatadogTransport(
        api_key="key-123", site="datadoghq.eu", sender=sender, max_records=2
    )
    logger = Logger("app.test", transports=[transport])

    logger.info("first")
    logger.info("second")

    assert len(sender.calls) == 1
    url, api_key, batch = sender.calls[0]
    assert url == "https://http-intake.logs.datadoghq.eu/api/v2/logs"
    assert api_key == "key-123"
    assert len(batch) == 2


def test_failing_send_is_caught_and_logged_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def failing_sender(url: str, api_key: str, batch: Sequence[str]) -> None:
        raise RuntimeError(f"DatadogTransport: request to {url} failed with status 403")

    transport = DatadogTransport(api_key="bad-key", sender=failing_sender, max_records=1)
    logger = Logger("app.test", transports=[transport])

    with caplog.at_level(logging.ERROR, logger="logquill"):
        logger.info("hello")  # must not raise

    assert "failed with status 403" in caplog.text
