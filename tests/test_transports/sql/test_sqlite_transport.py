from __future__ import annotations

from typing import Iterable, Sequence

from logquill.core.logger import Logger
from logquill.transports.sql.sqlite_transport import SQLiteConnectionLike, SQLiteTransport


class FakeSQLiteConnection:
    def __init__(self) -> None:
        self.exec_calls: list[str] = []
        self.executemany_calls: list[tuple[str, list[Sequence[object]]]] = []
        self.commit_count = 0

    def execute(self, sql: str, parameters: Sequence[object] = ()) -> None:
        self.exec_calls.append(sql)

    def executemany(self, sql: str, seq_of_parameters: Iterable[Sequence[object]]) -> None:
        self.executemany_calls.append((sql, list(seq_of_parameters)))

    def commit(self) -> None:
        self.commit_count += 1


def _fake() -> tuple[FakeSQLiteConnection, SQLiteConnectionLike]:
    connection = FakeSQLiteConnection()
    return connection, connection


def test_batches_inserts_and_never_creates_schema_by_default() -> None:
    connection, injected = _fake()
    transport = SQLiteTransport(connection=injected, max_records=2)
    logger = Logger("app.test", transports=[transport])

    logger.info("first")
    assert connection.executemany_calls == []
    logger.info("second")

    assert len(connection.executemany_calls) == 1
    assert len(connection.executemany_calls[0][1]) == 2
    assert connection.exec_calls == []


def test_ensure_schema_runs_ddl_exactly_once_across_reentrant_flushes() -> None:
    connection, injected = _fake()
    transport = SQLiteTransport(connection=injected, max_records=1, ensure_schema=True)
    logger = Logger("app.test", transports=[transport])

    logger.info("first")
    logger.info("second")

    assert len(connection.exec_calls) == 1
    assert "CREATE TABLE IF NOT EXISTS logs" in connection.exec_calls[0]


def test_close_flushes_partial_batch() -> None:
    connection, injected = _fake()
    transport = SQLiteTransport(connection=injected, max_records=100)
    logger = Logger("app.test", transports=[transport])

    logger.info("only one")
    logger.close()

    assert len(connection.executemany_calls) == 1
    assert connection.commit_count == 1


def test_row_carries_run_id_and_trace_id_from_meta() -> None:
    connection, injected = _fake()
    transport = SQLiteTransport(connection=injected, max_records=1)
    logger = Logger("app.test", transports=[transport])

    logger.info("traced", run_id="run-1", trace_id="trace-1")

    _, params = connection.executemany_calls[0]
    row = params[0]
    assert row[5] == "run-1"  # run_id column
    assert row[8] == "trace-1"  # trace_id column


def test_default_construction_uses_real_stdlib_sqlite3_in_memory() -> None:
    transport = SQLiteTransport(max_records=1, ensure_schema=True)
    logger = Logger("app.test", transports=[transport])

    logger.info("hello")  # must not raise — exercises the real sqlite3 path
    logger.close()
