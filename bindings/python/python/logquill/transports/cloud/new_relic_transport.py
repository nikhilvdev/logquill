from __future__ import annotations

import gzip
import json
import logging
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from typing import Callable, Literal, Sequence, TypedDict, cast

from logquill import _native
from logquill.core.records import LogRecord
from logquill.formatters import Formatter
from logquill.transports.batching_transport import BatchingTransport

NewRelicRegion = Literal["US", "EU"]

_URLS: dict[NewRelicRegion, str] = {
    "US": "https://log-api.newrelic.com/log/v1",
    "EU": "https://log-api.eu.newrelic.com/log/v1",
}

_logger = logging.getLogger("logquill")


class NewRelicSenderResult(TypedDict):
    """What a `NewRelicSender` reports back about one send attempt: whether
    it succeeded, the HTTP status, and (on a 429) the raw `Retry-After`
    header value, if any."""

    ok: bool
    status: int
    retry_after: str | None


NewRelicSender = Callable[[str, dict[str, str], bytes], NewRelicSenderResult]


def _urllib_sender(url: str, headers: dict[str, str], body: bytes) -> NewRelicSenderResult:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            response.read()
            return {"ok": True, "status": response.status, "retry_after": None}
    except urllib.error.HTTPError as exc:
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        return {"ok": False, "status": exc.code, "retry_after": retry_after}


def _without_event_type(record: LogRecord) -> LogRecord:
    meta = dict(record["meta"])
    meta.pop("eventType", None)
    return cast(LogRecord, {**record, "meta": meta})


def _resume_timestamp(retry_after: str | None, now: float) -> float:
    if retry_after is None:
        return now + 60.0
    try:
        return now + float(retry_after)
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(retry_after)
    except (TypeError, ValueError):
        return now + 60.0
    return parsed.timestamp()


class NewRelicTransport(BatchingTransport[LogRecord]):
    """Batches and gzips records before POSTing to New Relic's Log API via
    stdlib `urllib` + `gzip` — no client dependency. `region` selects the
    US or EU endpoint. Strips `meta["eventType"]` from every record — New
    Relic reserves and silently drops that key. On a 429 response, reads
    `Retry-After` (integer seconds or an HTTP-date) and pauses further
    sends until that time elapses, **dropping** (not requeuing) any batch
    flushed during the pause window, since New Relic blocks further sends
    for the rest of that minute on a rate-limit breach and retrying
    immediately just wastes calls. `clock` is injectable so the backoff is
    testable without real waits."""

    def __init__(
        self,
        *,
        license_key: str,
        region: NewRelicRegion = "US",
        formatter: Formatter | None = None,
        max_records: int = 100,
        max_bytes: int = 1_000_000,
        sender: NewRelicSender | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """`region` selects the US or EU ingestion endpoint. `sender`
        defaults to a stdlib `urllib`-based POST; override for a fake in
        tests. `clock` defaults to `time.time`; inject a fake to test the
        429 backoff window deterministically."""
        super().__init__(formatter=formatter, max_records=max_records, max_bytes=max_bytes)
        if region not in _URLS:
            raise ValueError(
                f"NewRelicTransport: region must be one of {', '.join(map(repr, _URLS))}, got {region!r}"
            )
        self.license_key = license_key
        self.region = region
        self.url = _URLS[region]
        self._sender: NewRelicSender = sender or _urllib_sender
        self._clock: Callable[[], float] = clock or time.time
        self._paused_until: float | None = None
        if sender is None and clock is None:
            # Done in Rust, including the pause after a 429. A test that injects a sender or a clock
            # keeps the Python path, which those hooks are for.
            self._native = _native.new_relic(license_key, region, max_records, max_bytes)
            self._native_dispatch = self._native

    def _send_batch(self, batch: Sequence[LogRecord]) -> None:
        now = self._clock()
        if self._paused_until is not None and now < self._paused_until:
            _logger.error(
                "NewRelicTransport: sends paused until %s after a 429 "
                "rate-limit response — skipping this batch rather than "
                "making a doomed request",
                self._paused_until,
            )
            return
        self._paused_until = None

        records = [_without_event_type(record) for record in batch]
        body = gzip.compress(json.dumps(records, separators=(",", ":"), default=str).encode())
        headers = {
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "Api-Key": self.license_key,
        }

        result = self._sender(self.url, headers, body)

        if result["status"] == 429:
            self._paused_until = _resume_timestamp(result["retry_after"], now)
            _logger.error(
                "NewRelicTransport: received 429 — pausing sends until %s",
                self._paused_until,
            )
            return
        if not result["ok"]:
            raise RuntimeError(
                f"NewRelicTransport: request to {self.url} failed with "
                f"status {result['status']} — check the license key and region"
            )
