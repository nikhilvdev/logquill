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


def test_the_document_carries_ecs_fields_alongside_logquills_own() -> None:
    sender = FakeSender()
    transport = ElasticsearchTransport(
        url="http://localhost:9200/", index="app-logs", sender=sender, max_records=1
    )
    logger = Logger("app.test", transports=[transport])

    logger.error("payment failed")

    _url, lines = sender.calls[0]
    source = json.loads(lines[1])
    assert source["message"] == "payment failed"  # LogQuill's own field, untouched
    assert source["@timestamp"] == source["timestamp"]
    assert source["log"] == {"level": "error"}
    assert source["service"] == {"name": "app.test"}


def test_trace_and_span_ids_map_to_ecs_trace_and_span() -> None:
    sender = FakeSender()
    transport = ElasticsearchTransport(url="http://localhost:9200/", sender=sender, max_records=1)
    logger = Logger("app.test", transports=[transport])

    logger.info("hello", trace_id="trace-1", span_id="span-1")

    _url, lines = sender.calls[0]
    source = json.loads(lines[1])
    assert source["trace"] == {"id": "trace-1"}
    assert source["span"] == {"id": "span-1"}


def test_no_trace_or_span_fields_when_absent() -> None:
    sender = FakeSender()
    transport = ElasticsearchTransport(url="http://localhost:9200/", sender=sender, max_records=1)
    logger = Logger("app.test", transports=[transport])

    logger.info("hello")

    _url, lines = sender.calls[0]
    source = json.loads(lines[1])
    assert "trace" not in source
    assert "span" not in source


def test_a_captured_exception_maps_to_ecs_error_fields() -> None:
    sender = FakeSender()
    transport = ElasticsearchTransport(url="http://localhost:9200/", sender=sender, max_records=1)
    logger = Logger("app.test", transports=[transport])

    try:
        raise ValueError("boom")
    except ValueError as exc:
        logger.error("request failed", exc_info=exc)

    _url, lines = sender.calls[0]
    source = json.loads(lines[1])
    assert source["error"]["message"] == "request failed"
    assert "ValueError: boom" in source["error"]["stack_trace"]


def test_no_error_field_without_a_captured_exception() -> None:
    sender = FakeSender()
    transport = ElasticsearchTransport(url="http://localhost:9200/", sender=sender, max_records=1)
    logger = Logger("app.test", transports=[transport])

    logger.info("hello")

    _url, lines = sender.calls[0]
    source = json.loads(lines[1])
    assert "error" not in source
