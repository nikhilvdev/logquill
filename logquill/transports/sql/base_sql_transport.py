from __future__ import annotations

import json
from abc import abstractmethod
from typing import Any, Sequence, TypedDict

from logquill.core.records import LogRecord
from logquill.formatters import Formatter
from logquill.transports.batching_transport import BatchingTransport


class SQLLogRow(TypedDict):
    """One row of the fixed `logs` table schema every SQL transport writes."""

    timestamp: str
    level: str
    logger: str
    message: str
    meta: str
    run_id: str | None
    span_id: str | None
    parent_span_id: str | None
    trace_id: str | None


def _meta_str(meta: dict[str, Any], key: str) -> str | None:
    value = meta.get(key)
    return value if isinstance(value, str) else None


class BaseSQLTransport(BatchingTransport[SQLLogRow]):
    """Shared base for every SQL sink: a fixed `logs` table schema and
    always-batched inserts — never one `INSERT` per log call, that defeats
    the point of buffering.

    Schema is never auto-created in production: `ensure_schema=True` is a
    dev/test-only convenience, and even then the DDL runs exactly once,
    before the first batch send, regardless of how many synchronous
    re-entrant flushes happen back to back (the "done" flag is set *before*
    the DDL runs, not after, so a second flush triggered while the first is
    still in flight can't re-run it).
    """

    def __init__(
        self,
        *,
        formatter: Formatter | None = None,
        max_records: int = 100,
        max_bytes: int = 1_000_000,
        table_name: str = "logs",
        ensure_schema: bool = False,
    ) -> None:
        """`ensure_schema=True` runs `create_table_sql()`'s DDL once, before
        the first batch send — a dev/test-only convenience, never enabled
        by default, since schema creation in production is the caller's
        responsibility (see the class docstring)."""
        super().__init__(formatter=formatter, max_records=max_records, max_bytes=max_bytes)
        self.table_name = table_name
        self.ensure_schema = ensure_schema
        self._schema_ensured = False

    def _to_item(self, formatted: str, record: LogRecord) -> SQLLogRow:
        meta = record["meta"]
        return SQLLogRow(
            timestamp=record["timestamp"],
            level=record["level"],
            logger=record["logger"],
            message=record["message"],
            meta=json.dumps(meta, separators=(",", ":"), default=str),
            run_id=_meta_str(meta, "run_id"),
            span_id=_meta_str(meta, "span_id"),
            parent_span_id=_meta_str(meta, "parent_span_id"),
            trace_id=_meta_str(meta, "trace_id"),
        )

    def _size_of(self, item: SQLLogRow) -> int:
        return len(item["message"].encode("utf-8")) + len(item["meta"].encode("utf-8")) + 96

    def _send_batch(self, batch: Sequence[SQLLogRow]) -> None:
        if self.ensure_schema and not self._schema_ensured:
            self._schema_ensured = True
            self._ensure_table()
        self._insert_rows(batch)

    def create_table_sql(self) -> str:
        """Dialect-generic `CREATE TABLE IF NOT EXISTS` DDL. Subclasses
        override for dialect-correct column types (e.g. Postgres `JSONB`)."""
        return (
            f"CREATE TABLE IF NOT EXISTS {self.table_name} ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "timestamp TEXT NOT NULL, "
            "level TEXT NOT NULL, "
            "logger TEXT NOT NULL, "
            "message TEXT NOT NULL, "
            "meta TEXT NOT NULL, "
            "run_id TEXT, "
            "span_id TEXT, "
            "parent_span_id TEXT, "
            "trace_id TEXT"
            ")"
        )

    @abstractmethod
    def _ensure_table(self) -> None: ...

    @abstractmethod
    def _insert_rows(self, rows: Sequence[SQLLogRow]) -> None: ...
