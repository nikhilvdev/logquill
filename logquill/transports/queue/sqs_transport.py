from __future__ import annotations

import json
from typing import Protocol, Sequence, cast

from logquill.core.records import LogRecord
from logquill.formatters import Formatter
from logquill.transports.queue.base_queue_transport import BaseQueueTransport

_SQS_BATCH_LIMIT = 10


class SQSClientLike(Protocol):
    """The subset of `boto3`'s SQS client this transport calls — implement
    this shape to inject a fake in tests without installing the real
    driver."""

    def send_message_batch(self, QueueUrl: str, Entries: Sequence[dict[str, str]]) -> object:  # noqa: N803
        """Send up to 10 messages to `QueueUrl` in one call."""
        ...


class SQSTransport(BaseQueueTransport):
    """Publishes to SQS via batched `send_message_batch` calls, chunked at
    the API's 10-message cap, through the optional `boto3` peer dependency
    (shared with `CloudWatchTransport`/`DynamoDBTransport`, since boto3 is
    one monolithic package). Chunks are dispatched **sequentially**, not
    concurrently — this project's dispatch is still fully synchronous
    project-wide (no non-blocking async worker exists yet), so true
    concurrent chunk dispatch is deferred until one does, rather than
    hand-rolled here with threads. `topic` is the queue URL. Pass `client`
    to inject a pre-built `boto3` SQS client (or a fake, for tests), or
    `region` to let this transport connect itself."""

    def __init__(
        self,
        *,
        topic: str,
        client: SQSClientLike | None = None,
        region: str | None = None,
        formatter: Formatter | None = None,
        max_records: int = 100,
        max_bytes: int = 1_000_000,
    ) -> None:
        """`region` is used only when this transport connects its own
        `boto3` client (ignored if `client` is given)."""
        super().__init__(
            topic=topic, formatter=formatter, max_records=max_records, max_bytes=max_bytes
        )
        self._injected = client
        self._region = region
        self._client: SQSClientLike | None = None

    def _resolved_client(self) -> SQSClientLike:
        if self._injected is not None:
            return self._injected
        if self._client is None:
            self._client = self._import_client()
        return self._client

    def _import_client(self) -> SQSClientLike:
        try:
            import boto3  # type: ignore  # stub availability for this optional dep varies by env
        except ImportError as exc:
            raise ImportError(
                "SQSTransport: install `boto3` to use this transport "
                "without providing a client — `pip install logquill[aws]`"
            ) from exc
        return cast(SQSClientLike, boto3.client("sqs", region_name=self._region))

    def _publish_batch(self, records: Sequence[LogRecord]) -> None:
        client = self._resolved_client()
        for start in range(0, len(records), _SQS_BATCH_LIMIT):
            chunk = records[start : start + _SQS_BATCH_LIMIT]
            entries = [
                {
                    "Id": str(index),
                    "MessageBody": json.dumps(record, separators=(",", ":"), default=str),
                }
                for index, record in enumerate(chunk)
            ]
            client.send_message_batch(QueueUrl=self.topic, Entries=entries)
