from __future__ import annotations

from typing import Any, ContextManager, Protocol, Sequence, cast

from logquill.core.records import LogRecord
from logquill.formatters import Formatter
from logquill.transports.batching_transport import BatchingTransport


class DynamoBatchWriterLike(Protocol):
    """The subset of `boto3`'s DynamoDB batch-writer context this transport
    calls — implement this shape to inject a fake in tests without
    installing the real driver."""

    def put_item(self, Item: dict[str, Any]) -> object:  # noqa: N803 — matches boto3's kwarg
        """Queue one item for the batch write."""
        ...


class DynamoTableLike(Protocol):
    """The subset of `boto3`'s DynamoDB `Table` resource this transport
    calls — implement this shape to inject a fake in tests without
    installing the real driver."""

    def batch_writer(self) -> ContextManager[DynamoBatchWriterLike]:
        """Open a batch-writer context that auto-chunks and auto-retries
        queued items."""
        ...


def _partition_key(record: LogRecord) -> str:
    meta = record["meta"]
    run_id = meta.get("run_id")
    if isinstance(run_id, str) and run_id:
        return run_id
    trace_id = meta.get("trace_id")
    if isinstance(trace_id, str) and trace_id:
        return trace_id
    return record["logger"]


def _to_item(record: LogRecord) -> dict[str, Any]:
    meta = record["meta"]
    item: dict[str, Any] = {
        "run_id": _partition_key(record),
        "timestamp": record["timestamp"],
        "level": record["level"],
        "logger": record["logger"],
        "message": record["message"],
        "meta": meta,
    }
    for key in ("span_id", "parent_span_id", "trace_id"):
        value = meta.get(key)
        if isinstance(value, str):
            item[key] = value
    return item


class DynamoDBTransport(BatchingTransport[LogRecord]):
    """Writes batches to DynamoDB via a `Table.batch_writer()` context
    manager — boto3 auto-marshals Python types, auto-chunks to the API's
    25-item `BatchWriteItem` cap, and auto-retries unprocessed items, so no
    hand-rolled `AttributeValue` marshalling or chunking is needed here.
    Partitions by `meta["run_id"]` (falling back to `meta["trace_id"]`,
    then the logger name) with `timestamp` as the sort key. Pass `table` to
    inject a pre-built `boto3` `Table` (or a fake, for tests), or
    `table_name`/`region` to let this transport connect itself via the
    optional `boto3` peer dependency."""

    def __init__(
        self,
        *,
        table: DynamoTableLike | None = None,
        table_name: str = "logs",
        region: str | None = None,
        formatter: Formatter | None = None,
        max_records: int = 100,
        max_bytes: int = 1_000_000,
    ) -> None:
        """`table_name`/`region` are used only when this transport connects
        its own `boto3` resource (ignored if `table` is given)."""
        super().__init__(formatter=formatter, max_records=max_records, max_bytes=max_bytes)
        self._injected = table
        self._table_name = table_name
        self._region = region
        self._table: DynamoTableLike | None = None

    def _resolved_table(self) -> DynamoTableLike:
        if self._injected is not None:
            return self._injected
        if self._table is None:
            self._table = self._import_table()
        return self._table

    def _import_table(self) -> DynamoTableLike:
        try:
            import boto3  # type: ignore  # stub availability for this optional dep varies by env
        except ImportError as exc:
            raise ImportError(
                "DynamoDBTransport: install `boto3` to use this transport "
                "without providing a table — `pip install logquill[aws]`"
            ) from exc
        resource = boto3.resource("dynamodb", region_name=self._region)
        return cast(DynamoTableLike, resource.Table(self._table_name))

    def _send_batch(self, batch: Sequence[LogRecord]) -> None:
        table = self._resolved_table()
        with table.batch_writer() as writer:
            for record in batch:
                writer.put_item(Item=_to_item(record))
