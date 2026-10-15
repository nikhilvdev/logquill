from __future__ import annotations

from typing import Any, Protocol, Sequence, cast

from logquill.core.records import LogRecord
from logquill.formatters import Formatter
from logquill.transports.batching_transport import BatchingTransport


class CloudWatchClientLike(Protocol):
    """The subset of `boto3`'s CloudWatch Logs client this transport calls —
    implement this shape to inject a fake in tests without installing the
    real driver."""

    def put_log_events(
        self,
        logGroupName: str,
        logStreamName: str,
        logEvents: Sequence[dict[str, Any]],  # noqa: N803
    ) -> object:
        """Send one batch of chronologically-ordered log events."""
        ...


def _to_millis(iso_timestamp: str) -> int:
    from datetime import datetime

    normalized = iso_timestamp[:-1] + "+00:00" if iso_timestamp.endswith("Z") else iso_timestamp
    return int(datetime.fromisoformat(normalized).timestamp() * 1000)


class CloudWatchTransport(BatchingTransport[LogRecord]):
    """Ships batched records to AWS CloudWatch Logs via `put_log_events`,
    through the optional `boto3` peer dependency (shared with
    `DynamoDBTransport`/`SQSTransport`). Sorts events by timestamp before
    each call, since the API requires events within a single
    `PutLogEvents` request to be in chronological order. Pass `client` to
    inject a pre-built `boto3` `logs` client (or a fake, for tests), or
    `region` to let this transport connect itself."""

    def __init__(
        self,
        *,
        log_group: str,
        log_stream: str,
        client: CloudWatchClientLike | None = None,
        region: str | None = None,
        formatter: Formatter | None = None,
        max_records: int = 100,
        max_bytes: int = 1_000_000,
    ) -> None:
        """`region` is the AWS region this transport connects its own
        `boto3` client to (ignored if `client` is given)."""
        super().__init__(formatter=formatter, max_records=max_records, max_bytes=max_bytes)
        self.log_group = log_group
        self.log_stream = log_stream
        self._injected = client
        self._region = region
        self._client: CloudWatchClientLike | None = None

    def _resolved_client(self) -> CloudWatchClientLike:
        if self._injected is not None:
            return self._injected
        if self._client is None:
            self._client = self._import_client()
        return self._client

    def _import_client(self) -> CloudWatchClientLike:
        try:
            import boto3  # type: ignore  # stub availability for this optional dep varies by env
        except ImportError as exc:
            raise ImportError(
                "CloudWatchTransport: install `boto3` to use this "
                "transport without providing a client — "
                "`pip install logquill[aws]`"
            ) from exc
        return cast(CloudWatchClientLike, boto3.client("logs", region_name=self._region))

    def _send_batch(self, batch: Sequence[LogRecord]) -> None:
        client = self._resolved_client()
        events = [
            {"timestamp": _to_millis(record["timestamp"]), "message": self.format(record)}
            for record in batch
        ]
        events.sort(key=lambda event: cast(int, event["timestamp"]))
        client.put_log_events(
            logGroupName=self.log_group, logStreamName=self.log_stream, logEvents=events
        )
