from __future__ import annotations

import json
import sys
from typing import Any

import pytest

from logquill import Logger, NeptuneTransport
from logquill.transports.graph.base_graph_transport import CYPHER


class FakeNeptuneClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def execute_open_cypher_query(self, *, openCypherQuery: str, parameters: str) -> dict[str, Any]:  # noqa: N803
        self.calls.append((openCypherQuery, parameters))
        return {"results": []}


def test_writes_use_the_opencypher_data_api_with_json_encoded_parameters() -> None:
    client = FakeNeptuneClient()
    transport = NeptuneTransport(client=client, max_records=100)
    logger = Logger("app", transports=[transport])

    logger.info("hello")
    logger.close()

    ((query, parameters),) = client.calls
    assert query == CYPHER
    decoded = json.loads(parameters)
    assert len(decoded["rows"]) == 1


def test_a_missing_boto3_package_gives_an_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "boto3", None)
    transport = NeptuneTransport()

    with pytest.raises(ImportError, match=r"pip install logquill\[aws\]"):
        transport._resolved_client()
