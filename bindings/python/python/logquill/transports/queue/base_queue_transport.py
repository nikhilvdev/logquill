from __future__ import annotations

from abc import abstractmethod
from typing import Sequence

from logquill.core.records import LogRecord
from logquill.formatters import Formatter
from logquill.transports.batching_transport import BatchingTransport


class BaseQueueTransport(BatchingTransport[LogRecord]):
    """Shared base for every message-queue sink: decouples log producers
    from consumers so multiple downstream systems (a SIEM, an analytics
    pipeline, an alerting system) can fan out from one topic, and buffers
    through a downstream outage instead of blocking or dropping. Always
    batches — never publishes one message per log call.
    """

    def __init__(
        self,
        *,
        topic: str,
        formatter: Formatter | None = None,
        max_records: int = 100,
        max_bytes: int = 1_000_000,
    ) -> None:
        """`topic` names the queue/topic this transport publishes to —
        interpreted per concrete backend (a Kafka topic name, an SQS queue
        URL, a fully-qualified Pub/Sub topic path, ...)."""
        super().__init__(formatter=formatter, max_records=max_records, max_bytes=max_bytes)
        self.topic = topic

    def _send_batch(self, batch: Sequence[LogRecord]) -> None:
        self._publish_batch(batch)

    @abstractmethod
    def _publish_batch(self, records: Sequence[LogRecord]) -> None: ...
