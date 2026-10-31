from __future__ import annotations

from typing import Protocol, Sequence, cast

from logquill.formatters import Formatter
from logquill.transports.sql.base_sql_transport import BaseSQLTransport, SQLLogRow


class MySQLCursorLike(Protocol):
    """The subset of `pymysql`'s cursor this transport calls — implement
    this shape to inject a fake in tests without installing the real
    driver."""

    def execute(self, sql: str, parameters: Sequence[object] = ()) -> object:
        """Execute one parameterized SQL statement."""
        ...


class MySQLConnectionLike(Protocol):
    """The subset of `pymysql`'s connection this transport calls —
    implement this shape to inject a fake in tests without installing the
    real driver."""

    def cursor(self) -> MySQLCursorLike:
        """Return a new cursor on this connection."""
        ...

    def commit(self) -> None:
        """Commit the current transaction."""
        ...

    def close(self) -> None:
        """Close the connection."""
        ...


class MySQLTransport(BaseSQLTransport):
    """Batches records into one parameterized multi-row `INSERT` per flush,
    via the optional `pymysql` peer dependency (pure Python — no C toolchain
    needed, unlike `mysqlclient`). Pass `dsn`-equivalent connection kwargs
    via a pre-built `connection`, or let this transport connect itself from
    `host`/`user`/`password`/`database`."""

    def __init__(
        self,
        *,
        connection: MySQLConnectionLike | None = None,
        host: str = "localhost",
        port: int = 3306,
        user: str = "root",
        password: str = "",
        database: str = "logquill",
        formatter: Formatter | None = None,
        max_records: int = 100,
        max_bytes: int = 1_000_000,
        table_name: str = "logs",
        ensure_schema: bool = False,
    ) -> None:
        """`host`/`port`/`user`/`password`/`database` are used only when
        this transport connects its own `pymysql` connection (ignored if
        `connection` is given)."""
        super().__init__(
            formatter=formatter,
            max_records=max_records,
            max_bytes=max_bytes,
            table_name=table_name,
            ensure_schema=ensure_schema,
        )
        self._injected = connection
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._database = database
        self._connection: MySQLConnectionLike | None = None

    dialect = "mysql"

    def _resolved_connection(self) -> MySQLConnectionLike:
        if self._injected is not None:
            return self._injected
        if self._connection is None:
            self._connection = self._import_connection()
        return self._connection

    def _import_connection(self) -> MySQLConnectionLike:
        try:
            import pymysql  # type: ignore  # stub availability for this optional dep varies by env
        except ImportError as exc:
            raise ImportError(
                "MySQLTransport: install `pymysql` to use this transport "
                "without providing a connection — `pip install logquill[mysql]`"
            ) from exc
        return cast(
            MySQLConnectionLike,
            pymysql.connect(
                host=self._host,
                port=self._port,
                user=self._user,
                password=self._password,
                database=self._database,
            ),
        )

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
        connection.cursor().execute(self.create_table_sql())
        connection.commit()

    def _insert_rows(self, rows: Sequence[SQLLogRow]) -> None:
        connection = self._resolved_connection()
        values_sql = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"] * len(rows))
        sql = (
            f"INSERT INTO {self.table_name} "
            "(timestamp, level, logger, message, meta, llm, run_id, span_id, parent_span_id, trace_id) "
            f"VALUES {values_sql}"
        )
        params: list[object] = []
        for row in rows:
            params.extend(
                [
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
                ]
            )
        connection.cursor().execute(sql, params)
        connection.commit()
