from __future__ import annotations

import json
from typing import Sequence

from logquill.core.logger import Logger
from logquill.transports.cloud.app_insights_transport import AppInsightsTransport


class FakeSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Sequence[str]]] = []

    def __call__(self, url: str, batch: Sequence[str]) -> None:
        self.calls.append((url, batch))


def test_sends_ndjson_trace_envelopes_with_instrumentation_key() -> None:
    sender = FakeSender()
    transport = AppInsightsTransport(instrumentation_key="ikey-123", sender=sender, max_records=2)
    logger = Logger("app.test", transports=[transport])

    logger.warn("careful", user_id="u-1")
    logger.fatal("boom")

    assert len(sender.calls) == 1
    url, lines = sender.calls[0]
    assert url == AppInsightsTransport.URL
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["iKey"] == "ikey-123"
    assert first["data"]["baseData"]["message"] == "careful"
    assert first["data"]["baseData"]["severityLevel"] == 2  # WARN
    assert first["data"]["baseData"]["properties"]["user_id"] == "u-1"

    second = json.loads(lines[1])
    assert second["data"]["baseData"]["severityLevel"] == 4  # FATAL
