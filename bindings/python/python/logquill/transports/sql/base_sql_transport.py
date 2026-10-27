from __future__ import annotations

import re
from abc import abstractmethod
from typing import Any, Sequence, TypedDict

from logquill import _native
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
    llm: str | None
    run_id: str | None
    span_id: str | None
    parent_span_id: str | None
    trace_id: str | None


#: The table name goes into SQL text, so only plain identifiers are accepted.
_TABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?")


class BaseSQLTransport(BatchingTransport[SQLLogRow]):
    #: The SQL dialect of the DDL: `"sqlite"`, `"postgres"` or `"mysql"`.
    dialect = "sqlite"

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
        if not _TABLE_NAME.fullmatch(table_name):
            raise ValueError(
                f"{type(self).__name__}: table_name {table_name!r} is not a plain SQL identifier "
                "\u2014 use letters, digits and underscores (optionally schema.table)"
            )
        self.table_name = table_name
        self.ensure_schema = ensure_schema
        self._schema_ensured = False

    def _to_item(self, formatted: str, record: LogRecord) -> SQLLogRow:
        return _native.sql_row(record)  # type: ignore[return-value,no-any-return]

    def _size_of(self, item: SQLLogRow) -> int:
        return len(item["message"].encode("utf-8")) + len(item["meta"].encode("utf-8")) + 96

    def _send_batch(self, batch: Sequence[SQLLogRow]) -> None:
        if self.ensure_schema and not self._schema_ensured:
            self._schema_ensured = True
            self._ensure_table()
        self._insert_rows(batch)

    def create_table_sql(self) -> str:
        """The DDL for the fixed log table in this transport's SQL dialect (from the Rust core)."""
        return _native.create_table_sql(self.dialect, self.table_name)  # type: ignore[no-any-return]

    @abstractmethod
    def _ensure_table(self) -> None: ...

    @abstractmethod
    def _insert_rows(self, rows: Sequence[SQLLogRow]) -> None: ...
