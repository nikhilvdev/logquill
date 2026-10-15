from __future__ import annotations

import urllib.error
import urllib.request
from typing import Callable, Sequence

from logquill.core.records import LogRecord
from logquill.formatters import Formatter
from logquill.transports.batching_transport import BatchingTransport

DatadogSender = Callable[[str, str, Sequence[str]], None]


def _urllib_sender(url: str, api_key: str, batch: Sequence[str]) -> None:
    body = ("[" + ",".join(batch) + "]").encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "DD-API-KEY": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"DatadogTransport: request to {url} failed with status "
            f"{exc.code} — check the API key and site region"
        ) from exc


class DatadogTransport(BatchingTransport[LogRecord]):
    """Batches records and POSTs them as a JSON array to Datadog's Logs
    intake API via stdlib `urllib` — no client dependency. `site`
    selects the region-specific endpoint (`datadoghq.com`, `datadoghq.eu`,
    `us3.datadoghq.com`, `us5.datadoghq.com`, `ap1.datadoghq.com`, ...).
    Pass `sender` to inject a fake for tests or an alternate transport."""

    def __init__(
        self,
        *,
        api_key: str,
        site: str = "datadoghq.com",
        formatter: Formatter | None = None,
        max_records: int = 100,
        max_bytes: int = 1_000_000,
        sender: DatadogSender | None = None,
    ) -> None:
        """`site` picks the region-specific intake endpoint — see the class
        docstring for valid values. `sender` defaults to a stdlib
        `urllib`-based POST; override for a fake in tests."""
        super().__init__(formatter=formatter, max_records=max_records, max_bytes=max_bytes)
        self.api_key = api_key
        self.site = site
        self.url = f"https://http-intake.logs.{site}/api/v2/logs"
        self._sender: DatadogSender = sender or _urllib_sender

    def _send_batch(self, batch: Sequence[LogRecord]) -> None:
        formatted = [self.format(record) for record in batch]
        self._sender(self.url, self.api_key, formatted)
