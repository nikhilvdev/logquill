from __future__ import annotations

from typing import Any, Protocol

from logquill.formatters import Formatter
from logquill.transports.graph.base_graph_transport import BaseGraphTransport


class FalkorDBGraphLike(Protocol):
    """The subset of `falkordb-py`'s selected-graph object this transport
    calls — implement this shape to inject a fake in tests without
    installing the real driver."""

    def query(self, q: str, params: dict[str, Any] | None = None) -> object:
        """Run one parameterized Cypher query against this graph."""
        ...


class FalkorDBClientLike(Protocol):
    """The subset of `falkordb-py`'s client this transport calls —
    implement this shape to inject a fake in tests without installing the
    real driver."""

    def select_graph(self, name: str) -> FalkorDBGraphLike:
        """Selects (creating if needed) the named graph."""
        ...

    def close(self) -> None:
        """Release the client's connection."""
        ...


class FalkorDBTransport(BaseGraphTransport):
    """Writes the span tree to FalkorDB — Redis-based and Cypher-compatible,
    the lightweight, self-hosted-friendly option in this category.

    Pass `client` to inject an already-constructed `falkordb.FalkorDB`
    client (or a fake, for tests), or `host`/`port`/`graph_name` to let this
    transport connect its own via the optional `falkordb` peer dependency
    (`pip install logquill[falkordb]`).
    """

    def __init__(
        self,
        *,
        client: FalkorDBClientLike | None = None,
        host: str = "localhost",
        port: int = 6379,
        graph_name: str = "logquill",
        formatter: Formatter | None = None,
        max_records: int = 100,
        max_bytes: int = 1_000_000,
        run_key: str = "run_id",
        trace_key: str = "trace_id",
    ) -> None:
        """`host`/`port`/`graph_name` are used only when this transport
        connects its own `falkordb` client (ignored if `client` is given)."""
        super().__init__(
            formatter=formatter,
            max_records=max_records,
            max_bytes=max_bytes,
            run_key=run_key,
            trace_key=trace_key,
        )
        self._injected = client
        self._host = host
        self._port = port
        self._graph_name = graph_name
        self._client: FalkorDBClientLike | None = None
        self._graph: FalkorDBGraphLike | None = None

    def _resolved_graph(self) -> FalkorDBGraphLike:
        if self._graph is not None:
            return self._graph
        client = self._injected
        if client is None:
            if self._client is None:
                self._client = self._connect()
            client = self._client
        self._graph = client.select_graph(self._graph_name)
        return self._graph

    def _connect(self) -> FalkorDBClientLike:
        try:
            from falkordb import FalkorDB  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                "FalkorDBTransport requires the optional `falkordb` package — install with "
                "`pip install logquill[falkordb]`."
            ) from exc
        return FalkorDB(host=self._host, port=self._port)  # type: ignore[no-any-return]

    def _execute(self, query: str, parameters: dict[str, Any]) -> None:
        self._resolved_graph().query(query, parameters)

    def close(self) -> None:
        """Flushes any remaining buffered records, then releases the
        client's connection (only if this transport connected it itself —
        an injected `client` is left for its owner to close)."""
        super().close()
        if self._injected is None and self._client is not None:
            self._client.close()
