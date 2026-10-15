from __future__ import annotations

import sys
from typing import Any

import pytest

from logquill import FalkorDBTransport, Logger
from logquill.transports.graph.base_graph_transport import CYPHER


class FakeGraph:
    def __init__(self, calls: list[tuple[str, dict[str, Any]]]) -> None:
        self._calls = calls

    def query(self, q: str, params: dict[str, Any] | None = None) -> None:
        self._calls.append((q, params or {}))


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False
        self.selected: list[str] = []

    def select_graph(self, name: str) -> FakeGraph:
        self.selected.append(name)
        return FakeGraph(self.calls)

    def close(self) -> None:
        self.closed = True


def test_writes_go_through_the_selected_graph_with_the_shared_cypher() -> None:
    client = FakeClient()
    transport = FalkorDBTransport(client=client, graph_name="agents", max_records=100)
    logger = Logger("app", transports=[transport])

    logger.info("hello")
    logger.close()

    ((query, parameters),) = client.calls
    assert query == CYPHER
    assert len(parameters["rows"]) == 1
    assert client.selected == ["agents"]


def test_the_graph_is_selected_only_once() -> None:
    client = FakeClient()
    transport = FalkorDBTransport(client=client, max_records=1)
    logger = Logger("app", transports=[transport])

    logger.info("one")
    logger.info("two")

    assert client.selected == ["logquill"]


def test_close_does_not_close_an_injected_client() -> None:
    client = FakeClient()
    transport = FalkorDBTransport(client=client)

    transport.close()

    assert client.closed is False


def test_a_missing_falkordb_package_gives_an_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "falkordb", None)
    transport = FalkorDBTransport()

    with pytest.raises(ImportError, match=r"pip install logquill\[falkordb\]"):
        transport._resolved_graph()
