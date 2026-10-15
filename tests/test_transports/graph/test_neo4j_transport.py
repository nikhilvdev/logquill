from __future__ import annotations

import sys
from typing import Any

import pytest

from logquill import Logger, Neo4jTransport
from logquill.transports.graph.base_graph_transport import CYPHER


class FakeSession:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]]) -> None:
        self._calls = calls

    def run(self, query: str, parameters: dict[str, Any]) -> None:
        self._calls.append((query, parameters))

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass


class FakeDriver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False
        self.session_kwargs: list[dict[str, Any]] = []

    def session(self, **kwargs: Any) -> FakeSession:
        self.session_kwargs.append(kwargs)
        return FakeSession(self.calls)

    def close(self) -> None:
        self.closed = True


def test_writes_go_through_a_session_with_the_shared_cypher() -> None:
    driver = FakeDriver()
    transport = Neo4jTransport(driver=driver, max_records=100)
    logger = Logger("app", transports=[transport])

    logger.info("hello")
    logger.close()

    ((query, parameters),) = driver.calls
    assert query == CYPHER
    assert len(parameters["rows"]) == 1


def test_database_is_forwarded_to_the_session() -> None:
    driver = FakeDriver()
    transport = Neo4jTransport(driver=driver, database="agents", max_records=1)
    Logger("app", transports=[transport]).info("hello")

    assert driver.session_kwargs == [{"database": "agents"}]


def test_no_database_means_no_database_kwarg() -> None:
    driver = FakeDriver()
    transport = Neo4jTransport(driver=driver, max_records=1)
    Logger("app", transports=[transport]).info("hello")

    assert driver.session_kwargs == [{}]


def test_close_does_not_close_an_injected_driver() -> None:
    driver = FakeDriver()
    transport = Neo4jTransport(driver=driver)

    transport.close()

    assert driver.closed is False


def test_a_missing_neo4j_package_gives_an_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "neo4j", None)
    transport = Neo4jTransport()

    with pytest.raises(ImportError, match=r"pip install logquill\[neo4j\]"):
        transport._resolved_driver()
