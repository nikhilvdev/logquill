from __future__ import annotations

import json
import urllib.request
from typing import Callable, Sequence

from logquill.core.records import LogRecord
from logquill.formatters import Formatter
from logquill.transports.batching_transport import BatchingTransport

Sender = Callable[[str, Sequence[str]], None]

_SEVERITY = {"TRACE": 0, "DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3, "FATAL": 4}


def _urllib_sender(url: str, batch: Sequence[str]) -> None:
    body = "\n".join(batch).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-json-stream"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        response.read()


class AppInsightsTransport(BatchingTransport[LogRecord]):
    """Ships records to Azure Application Insights as trace telemetry.

    Deliberately diverges from logquill-js's SDK-based approach: Application
    Insights has a documented public ingestion endpoint
    (`https://dc.services.visualstudio.com/v2/track`), so this posts
    newline-delimited telemetry envelopes there directly via stdlib
    `urllib` instead of pulling in an Azure SDK dependency. Same outward
    behavior — records land as trace telemetry — zero new dependency.
    """

    URL = "https://dc.services.visualstudio.com/v2/track"

    def __init__(
        self,
        *,
        instrumentation_key: str,
        formatter: Formatter | None = None,
        max_records: int = 100,
        max_bytes: int = 1_000_000,
        sender: Sender | None = None,
    ) -> None:
        """`instrumentation_key` is the Application Insights resource's
        instrumentation key. `sender` defaults to a stdlib `urllib`-based
        POST; override for a fake in tests."""
        super().__init__(formatter=formatter, max_records=max_records, max_bytes=max_bytes)
        self.instrumentation_key = instrumentation_key
        self._sender: Sender = sender or _urllib_sender

    def _envelope(self, record: LogRecord) -> str:
        return json.dumps(
            {
                "name": "Microsoft.ApplicationInsights.Trace",
                "time": record["timestamp"],
                "iKey": self.instrumentation_key,
                "data": {
                    "baseType": "MessageData",
                    "baseData": {
                        "ver": 2,
                        "message": record["message"],
                        "severityLevel": _SEVERITY.get(record["level"], 1),
                        "properties": {k: str(v) for k, v in record["meta"].items()},
                    },
                },
            },
            separators=(",", ":"),
            default=str,
        )

    def _send_batch(self, batch: Sequence[LogRecord]) -> None:
        lines = [self._envelope(record) for record in batch]
        self._sender(self.URL, lines)
