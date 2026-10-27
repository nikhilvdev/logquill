from __future__ import annotations

import json
from typing import Any, Protocol

from logquill.formatters import Formatter
from logquill.transports.graph.base_graph_transport import BaseGraphTransport


class NeptuneDataClientLike(Protocol):
    """The subset of `boto3`'s `neptunedata` client this transport calls —
    implement this shape to inject a fake in tests without installing the
    real SDK or reaching a real cluster."""

    def execute_open_cypher_query(self, *, openCypherQuery: str, parameters: str) -> object:  # noqa: N803
        """Runs one openCypher query; `parameters` is JSON-encoded, per the
        `neptunedata` API's own shape — not a dict."""
        ...


class NeptuneTransport(BaseGraphTransport):
    """Writes the span tree to Amazon Neptune via its openCypher HTTPS data
    API (the `neptunedata` service), sitting alongside this project's other
    AWS-native transports (`CloudWatchTransport`, `DynamoDBTransport`, ...).

    Pass `client` to inject an already-constructed `boto3` `neptunedata`
    client (or a fake, for tests), or `endpoint_url`/`region_name` to let
    this transport build its own via the optional `boto3` peer dependency
    (`pip install logquill[aws]` — the same extra the other AWS transports
    use). Reaching `endpoint_url` normally requires running from inside the
    cluster's VPC (or wherever has network access to it) — that's a Neptune
    deployment detail, not something this transport can work around.
    """

    def __init__(
        self,
        *,
        client: NeptuneDataClientLike | None = None,
        endpoint_url: str | None = None,
        region_name: str | None = None,
        formatter: Formatter | None = None,
        max_records: int = 100,
        max_bytes: int = 1_000_000,
        run_key: str = "run_id",
        trace_key: str = "trace_id",
    ) -> None:
        """`endpoint_url`/`region_name` are used only when this transport
        builds its own `boto3` client (ignored if `client` is given)."""
        super().__init__(
            formatter=formatter,
            max_records=max_records,
            max_bytes=max_bytes,
            run_key=run_key,
            trace_key=trace_key,
        )
        self._injected = client
        self._endpoint_url = endpoint_url
        self._region_name = region_name
        self._client: NeptuneDataClientLike | None = None

    def _resolved_client(self) -> NeptuneDataClientLike:
        if self._injected is not None:
            return self._injected
        if self._client is None:
            self._client = self._connect()
        return self._client

    def _connect(self) -> NeptuneDataClientLike:
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                "NeptuneTransport requires the optional `boto3` package — install with "
                "`pip install logquill[aws]`."
            ) from exc
        return boto3.client(  # type: ignore[no-any-return]
            "neptunedata", endpoint_url=self._endpoint_url, region_name=self._region_name
        )

    def _execute(self, query: str, parameters: dict[str, Any]) -> None:
        self._resolved_client().execute_open_cypher_query(
            openCypherQuery=query, parameters=json.dumps(parameters, default=str)
        )
