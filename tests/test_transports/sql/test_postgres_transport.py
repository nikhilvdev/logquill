from __future__ import annotations

import logging

import pytest

from logquill.core.logger import Logger
from logquill.transports.sql.postgres_transport import PostgresCursorLike, PostgresTransport


class FakeCursor:
    def __init__(self, sink: list[tuple[str, object]]) -> None:
        self._sink = sink

    def execute(self, sql: str, parameters: object = ()) -> None:
        self._sink.append((sql, parameters))


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.commit_count = 0

    def cursor(self) -> PostgresCursorLike:
        return FakeCursor(self.calls)

    def commit(self) -> None:
        self.commit_count += 1


def test_batches_multi_row_insert_with_jsonb_cast() -> None:
    fake = FakeConnection()
    transport = PostgresTransport(connection=fake, max_records=2)
    logger = Logger("app.test", transports=[transport])

    logger.info("first")
    logger.info("second")

    assert len(fake.calls) == 1
    sql, params = fake.calls[0]
    assert sql.count("::jsonb") == 2
    assert len(params) == 18  # 9 columns * 2 rows
    assert fake.commit_count == 1


def test_missing_dependency_logs_actionable_install_hint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = PostgresTransport(max_records=1)
    logger = Logger("app.test", transports=[transport])

    with caplog.at_level(logging.ERROR, logger="logquill"):
        logger.info("hello")  # must not raise

    assert "pip install logquill[postgres]" in caplog.text
