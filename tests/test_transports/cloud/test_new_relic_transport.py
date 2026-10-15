from __future__ import annotations

import gzip
import json
from typing import Callable

from logquill.core.logger import Logger
from logquill.transports.cloud.new_relic_transport import (
    NewRelicSenderResult,
    NewRelicTransport,
)


class FakeSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], bytes]] = []
        self.results: list[NewRelicSenderResult] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes) -> NewRelicSenderResult:
        self.calls.append((url, headers, body))
        if self.results:
            return self.results.pop(0)
        return {"ok": True, "status": 202, "retry_after": None}


def _clock(values: list[float]) -> Callable[[], float]:
    def clock() -> float:
        return values.pop(0)

    return clock


def test_region_url_gzip_body_and_headers() -> None:
    sender = FakeSender()
    transport = NewRelicTransport(license_key="lk-1", region="EU", sender=sender, max_records=1)
    logger = Logger("app.test", transports=[transport])

    logger.info("hello", eventType="Custom", extra="kept")

    assert len(sender.calls) == 1
    url, headers, body = sender.calls[0]
    assert url == "https://log-api.eu.newrelic.com/log/v1"
    assert headers["Api-Key"] == "lk-1"
    assert headers["Content-Encoding"] == "gzip"

    records = json.loads(gzip.decompress(body))
    assert "eventType" not in records[0]["meta"]
    assert records[0]["meta"]["extra"] == "kept"


def test_stripping_event_type_keeps_the_schema_version_and_llm_block() -> None:
    from logquill import Level
    from logquill.core.records import create_record

    sender = FakeSender()
    transport = NewRelicTransport(license_key="lk-1", sender=sender, max_records=1)
    record = create_record(
        level=Level.INFO,
        logger="app.test",
        message="chat",
        meta={"eventType": "Custom"},
        llm={"model": "m", "tokens_in": 3},
    )

    transport.write("", record)

    (sent,) = json.loads(gzip.decompress(sender.calls[0][2]))
    assert sent["schema_version"] == "2.0"
    assert sent["llm"] == {"model": "m", "tokens_in": 3}
    assert "eventType" not in sent["meta"]


def test_429_pauses_sends_and_drops_batches_during_the_window() -> None:
    sender = FakeSender()
    sender.results = [{"ok": False, "status": 429, "retry_after": "60"}]
    clock_values = [1000.0, 1010.0, 1070.0]
    transport = NewRelicTransport(
        license_key="lk-1", sender=sender, clock=_clock(clock_values), max_records=1
    )
    logger = Logger("app.test", transports=[transport])

    logger.info("first")  # 429 at t=1000, pause until t=1060
    assert len(sender.calls) == 1

    logger.info("second")  # t=1010, still paused -> dropped, no send
    assert len(sender.calls) == 1

    logger.info("third")  # t=1070, past pause -> sends again
    assert len(sender.calls) == 2


def test_missing_retry_after_defaults_to_sixty_second_pause() -> None:
    sender = FakeSender()
    sender.results = [{"ok": False, "status": 429, "retry_after": None}]
    clock_values = [0.0, 59.0, 61.0]
    transport = NewRelicTransport(
        license_key="lk-1", sender=sender, clock=_clock(clock_values), max_records=1
    )
    logger = Logger("app.test", transports=[transport])

    logger.info("first")
    assert len(sender.calls) == 1
    logger.info("second")  # still within default 60s pause
    assert len(sender.calls) == 1
    logger.info("third")  # past the 60s pause
    assert len(sender.calls) == 2
