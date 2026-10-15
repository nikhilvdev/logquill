from __future__ import annotations

import logging
from typing import Any

import pytest

from logquill.core.logger import Logger
from logquill.transports.cloud.cloud_logging_transport import CloudLoggingTransport


class FakeCloudLoggingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], str]] = []

    def log_struct(self, info: dict[str, Any], severity: str) -> None:
        self.calls.append((info, severity))


def test_maps_level_onto_severity_scale() -> None:
    client = FakeCloudLoggingClient()
    transport = CloudLoggingTransport(client=client, max_records=1)
    logger = Logger("app.test", transports=[transport])

    logger.warn("careful")
    logger.fatal("boom")

    assert client.calls[0][1] == "WARNING"
    assert client.calls[1][1] == "CRITICAL"


def test_missing_dependency_logs_actionable_install_hint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = CloudLoggingTransport(max_records=1)
    logger = Logger("app.test", transports=[transport])

    with caplog.at_level(logging.ERROR, logger="logquill"):
        logger.info("hello")  # must not raise

    assert "pip install logquill[gcp-logging]" in caplog.text
