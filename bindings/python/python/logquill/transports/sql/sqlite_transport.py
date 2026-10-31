from __future__ import annotations

import sqlite3
from typing import Iterable, Protocol, Sequence

from logquill import _native
from logquill.formatters import Formatter
from logquill.transports.sql.base_sql_transport import BaseSQLTransport, SQLLogRow


class SQLiteConnectionLike(Protocol):
    """The subset of stdlib `sqlite3`'s connection this transport calls —
    implement this shape to inject a fake in tests."""

    def execute(self, sql: str, parameters: Sequence[object] = ()) -> object:
        """Execute one parameterized SQL statement."""
        ...

    def executemany(self, sql: str, seq_of_parameters: Iterable[Sequence[object]]) -> object:
        """Execute one SQL statement against many parameter sequences."""
        ...

    def commit(self) -> None:
        """Commit the current transaction."""
        ...

    def close(self) -> None:
        """Close the connection."""
        ...


class SQLiteTransport(BaseSQLTransport):
    """Zero-setup SQL sink via stdlib `sqlite3` — no server process, no
    optional dependency, works against a file path or `:memory:`. Pass
    `connection` to inject an already-open connection for tests or an
    alternate setup."""

    def __init__(
        self,
        *,
        connection: SQLiteConnectionLike | None = None,
        filename: str = ":memory:",
        formatter: Formatter | None = None,
        max_records: int = 100,
        max_bytes: int = 1_000_000,
        table_name: str = "logs",
        ensure_schema: bool = False,
    ) -> None:
        """`filename` is used only when this transport connects its own
        `sqlite3` connection (ignored if `connection` is given); defaults to
        an in-memory database."""
        super().__init__(
            formatter=formatter,
            max_records=max_records,
            max_bytes=max_bytes,
            table_name=table_name,
            ensure_schema=ensure_schema,
        )
        self._injected = connection
        self._filename = filename
        self._connection: SQLiteConnectionLike | None = None
        if connection is None:
            # Done in Rust: one multi-row INSERT per batch inside a transaction, WAL mode so the
            # viewer can read while this writes. A caller's own `connection` keeps the Python path.
            self._native = _native.sqlite(filename, table_name, ensure_schema, max_records, max_bytes)
            self._native_dispatch = self._native

    def _resolved_connection(self) -> SQLiteConnectionLike:
        if self._injected is not None:
            return self._injected
        if self._connection is None:
            self._connection = sqlite3.connect(self._filename)
        return self._connection

    def close(self) -> None:
        """Flushes any remaining buffered records, then closes the
        self-connected connection — never a connection passed in as
        `connection`, since this transport doesn't own that connection's
        lifecycle."""
        super().close()
        if self._injected is None and self._connection is not None:
            self._connection.close()

    def _ensure_table(self) -> None:
        connection = self._resolved_connection()
        connection.execute(self.create_table_sql())
        connection.commit()

    def _insert_rows(self, rows: Sequence[SQLLogRow]) -> None:
        connection = self._resolved_connection()
        sql = (
            f"INSERT INTO {self.table_name} "
            "(timestamp, level, logger, message, meta, llm, run_id, span_id, parent_span_id, trace_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        connection.executemany(
            sql,
            [
                (
                    row["timestamp"],
                    row["level"],
                    row["logger"],
                    row["message"],
                    row["meta"],
                    row["llm"],
                    row["run_id"],
                    row["span_id"],
                    row["parent_span_id"],
                    row["trace_id"],
                )
                for row in rows
            ],
        )
        connection.commit()
