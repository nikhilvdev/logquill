from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Sequence

from logquill.core.records import LogRecord
from logquill.formatters import Formatter
from logquill.transports.batching_transport import BatchingTransport

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
    transport."""

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

    def _send_batch(self, batch: Sequence[LogRecord]) -> None:
        lines: list[str] = []
        for record in batch:
            lines.append(json.dumps({"index": {"_index": self.index}}, separators=(",", ":")))
            lines.append(self.format(record))
        self._sender(self.bulk_url, lines)
