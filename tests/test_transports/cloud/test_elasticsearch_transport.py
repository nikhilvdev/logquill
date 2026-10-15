from __future__ import annotations

import json
from typing import Sequence

from logquill.core.logger import Logger
from logquill.transports.cloud.elasticsearch_transport import ElasticsearchTransport


class FakeSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Sequence[str]]] = []

    def __call__(self, url: str, batch: Sequence[str]) -> None:
        self.calls.append((url, batch))


def test_sends_ndjson_action_and_source_pairs_to_bulk_endpoint() -> None:
    sender = FakeSender()
    transport = ElasticsearchTransport(
        url="http://localhost:9200/", index="app-logs", sender=sender, max_records=1
    )
    logger = Logger("app.test", transports=[transport])

    logger.info("hello")

    assert len(sender.calls) == 1
    url, lines = sender.calls[0]
    assert url == "http://localhost:9200/_bulk"
    assert len(lines) == 2
    action = json.loads(lines[0])
    assert action == {"index": {"_index": "app-logs"}}
    source = json.loads(lines[1])
    assert source["message"] == "hello"
