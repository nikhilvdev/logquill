from __future__ import annotations

import json
from typing import Protocol, Sequence, cast

from logquill.core.records import LogRecord
from logquill.formatters import Formatter
from logquill.transports.queue.base_queue_transport import BaseQueueTransport


class PubSubFutureLike(Protocol):
    """The subset of the future `google-cloud-pubsub`'s `publish()` returns
    this transport calls — implement this shape to inject a fake in tests
    without installing the real driver."""

    def result(self) -> object:
        """Block until the publish completes, raising on failure."""
        ...


class PubSubTopicLike(Protocol):
    """The subset of `google-cloud-pubsub`'s `PublisherClient` this
    transport calls — implement this shape to inject a fake in tests
    without installing the real driver."""

    def publish(self, topic: str, data: bytes) -> PubSubFutureLike:
        """Publish one message to `topic`, returning a future for its
        result."""
        ...


class PubSubTransport(BaseQueueTransport):
    """Publishes to GCP Pub/Sub via the optional `google-cloud-pubsub` peer
    dependency. `topic` is the fully-qualified topic path (e.g.
    `projects/<project>/topics/<topic>`). Each record's publish future is
    waited on before the flush returns, keeping this transport's dispatch
    synchronous like every other transport in the project today. Pass
    `client` to inject a pre-built `PublisherClient` (or a fake, for
    tests)."""

    def __init__(
        self,
        *,
        topic: str,
        client: PubSubTopicLike | None = None,
        formatter: Formatter | None = None,
        max_records: int = 100,
        max_bytes: int = 1_000_000,
    ) -> None:
        """Connects its own `PublisherClient` lazily on first use unless
        `client` is given."""
        super().__init__(
            topic=topic, formatter=formatter, max_records=max_records, max_bytes=max_bytes
        )
        self._injected = client
        self._client: PubSubTopicLike | None = None

    def _resolved_client(self) -> PubSubTopicLike:
        if self._injected is not None:
            return self._injected
        if self._client is None:
            self._client = self._import_client()
        return self._client

    def _import_client(self) -> PubSubTopicLike:
        try:
            from google.cloud import pubsub_v1  # type: ignore  # stub availability varies by env
        except ImportError as exc:
            raise ImportError(
                "PubSubTransport: install `google-cloud-pubsub` to use "
                "this transport without providing a client — "
                "`pip install logquill[pubsub]`"
            ) from exc
        return cast(PubSubTopicLike, pubsub_v1.PublisherClient())

    def _publish_batch(self, records: Sequence[LogRecord]) -> None:
        client = self._resolved_client()
        futures = [
            client.publish(
                self.topic, data=json.dumps(record, separators=(",", ":"), default=str).encode()
            )
            for record in records
        ]
        for future in futures:
            future.result()
