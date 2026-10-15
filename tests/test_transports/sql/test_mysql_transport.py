from __future__ import annotations

import logging

import pytest

from logquill.core.logger import Logger
from logquill.transports.sql.mysql_transport import MySQLCursorLike, MySQLTransport


class FakeCursor:
    def __init__(self, sink: list[tuple[str, object]]) -> None:
        self._sink = sink

    def execute(self, sql: str, parameters: object = ()) -> None:
        self._sink.append((sql, parameters))


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.commit_count = 0

    def cursor(self) -> MySQLCursorLike:
        return FakeCursor(self.calls)

    def commit(self) -> None:
        self.commit_count += 1


def test_batches_multi_row_insert() -> None:
    fake = FakeConnection()
    transport = MySQLTransport(connection=fake, max_records=2)
    logger = Logger("app.test", transports=[transport])

    logger.info("first")
    logger.info("second")

    assert len(fake.calls) == 1
    sql, params = fake.calls[0]
    assert sql.startswith("INSERT INTO logs")
    assert len(params) == 18  # 9 columns * 2 rows
    assert fake.commit_count == 1


def test_missing_dependency_logs_actionable_install_hint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = MySQLTransport(max_records=1)
    logger = Logger("app.test", transports=[transport])

    with caplog.at_level(logging.ERROR, logger="logquill"):
        logger.info("hello")  # must not raise

    assert "pip install logquill[mysql]" in caplog.text
