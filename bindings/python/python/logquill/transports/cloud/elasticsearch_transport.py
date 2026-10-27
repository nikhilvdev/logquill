from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Sequence

from logquill import _native
from logquill.core.records import LogRecord
from logquill.formatters import Formatter
from logquill.transports.batching_transport import BatchingTransport
from logquill.transports.cloud.ecs_mapper import to_ecs_document

ElasticsearchSender = Callable[[str, Sequence[str]], None]


def _urllib_sender(url: str, batch: Sequence[str]) -> None:
    body = ("\n".join(batch) + "\n").encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-ndjson"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"ElasticsearchTransport: request to {url} failed with status "
            f"{exc.code} — check the URL and index"
        ) from exc


class ElasticsearchTransport(BatchingTransport[LogRecord]):
    """Batches records and POSTs them to Elasticsearch's `_bulk` API as
    newline-delimited action+source pairs via stdlib `urllib` — no client
    dependency. Pass `sender` to inject a fake for tests or an alternate
    transport.

    This is where Kibana support actually lives — Kibana never ingests
    data itself, it only visualizes what's already in Elasticsearch. Each
    document is LogQuill's own record with [Elastic Common
    Schema](https://www.elastic.co/guide/en/ecs/current/ecs-reference.html)
    fields added alongside it (see `logquill.transports.cloud.ecs_mapper`),
    so Kibana's default Discover view, dashboards, and alerting rules work
    on this data with zero manual field mapping. The document body is
    always JSON, built directly from the ECS mapping rather than through
    `formatter` (inherited from `BatchingTransport`) — Elasticsearch's
    bulk API requires a JSON object per line, so a non-JSON formatter
    configured here wouldn't produce a valid request body anyway.
    """

    def __init__(
        self,
        *,
        url: str,
        index: str = "logs",
        formatter: Formatter | None = None,
        max_records: int = 100,
        max_bytes: int = 1_000_000,
        sender: ElasticsearchSender | None = None,
    ) -> None:
        """`sender` defaults to a stdlib `urllib`-based POST; override for a
        fake in tests."""
        super().__init__(formatter=formatter, max_records=max_records, max_bytes=max_bytes)
        self.index = index
        self.bulk_url = f"{url.rstrip('/')}/_bulk"
        self._sender: ElasticsearchSender = sender or _urllib_sender
        if sender is None:
            self._native = _native.elasticsearch(url, index, max_records, max_bytes)
            self._native_dispatch = self._native

    def _send_batch(self, batch: Sequence[LogRecord]) -> None:
        lines: list[str] = []
        for record in batch:
            lines.append(json.dumps({"index": {"_index": self.index}}, separators=(",", ":")))
            document = to_ecs_document(record)
            lines.append(json.dumps(document, separators=(",", ":"), default=str))
        self._sender(self.bulk_url, lines)
