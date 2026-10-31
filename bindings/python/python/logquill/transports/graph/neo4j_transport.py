from __future__ import annotations

from typing import Any, ContextManager, Protocol

from logquill.formatters import Formatter
from logquill.transports.graph.base_graph_transport import BaseGraphTransport


class Neo4jSessionLike(Protocol):
    """The subset of the `neo4j` driver's `Session` this transport calls —
    implement this shape to inject a fake in tests without installing the
    real driver."""

    def run(self, query: str, parameters: dict[str, Any]) -> object:
        """Run one parameterized Cypher query."""
        ...


class Neo4jDriverLike(Protocol):
    """The subset of the `neo4j` driver's `Driver` this transport calls —
    implement this shape to inject a fake in tests without installing the
    real driver."""

    def session(self, **kwargs: Any) -> ContextManager[Neo4jSessionLike]:
        """Open a session scoped to one database/transaction context."""
        ...

    def close(self) -> None:
        """Release the driver's connection pool."""
        ...


class Neo4jTransport(BaseGraphTransport):
    """Writes the span tree to Neo4j — the reference/most widely deployed
    backend in this category, and the first one implemented here.

    Pass `driver` to inject an already-constructed `neo4j.GraphDatabase`
    driver (or a fake, for tests), or `uri`/`auth`/`database` to let this
    transport connect its own via the optional `neo4j` peer dependency
    (`pip install logquill[neo4j]`).
    """

    def __init__(
        self,
        *,
        driver: Neo4jDriverLike | None = None,
        uri: str = "bolt://localhost:7687",
        auth: tuple[str, str] | None = None,
        database: str | None = None,
        formatter: Formatter | None = None,
        max_records: int = 100,
        max_bytes: int = 1_000_000,
        run_key: str = "run_id",
        trace_key: str = "trace_id",
    ) -> None:
        """`uri`/`auth`/`database` are used only when this transport
        connects its own `neo4j` driver (ignored if `driver` is given)."""
        super().__init__(
            formatter=formatter,
            max_records=max_records,
            max_bytes=max_bytes,
            run_key=run_key,
            trace_key=trace_key,
        )
        self._injected = driver
        self._uri = uri
        self._auth = auth
        self._database = database
        self._driver: Neo4jDriverLike | None = None

    def _resolved_driver(self) -> Neo4jDriverLike:
        if self._injected is not None:
            return self._injected
        if self._driver is None:
            self._driver = self._connect()
        return self._driver

    def _connect(self) -> Neo4jDriverLike:
        try:
            from neo4j import GraphDatabase  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                "Neo4jTransport requires the optional `neo4j` package — install with "
                "`pip install logquill[neo4j]`."
            ) from exc
        return GraphDatabase.driver(self._uri, auth=self._auth)  # type: ignore[no-any-return]

    def _execute(self, query: str, parameters: dict[str, Any]) -> None:
        session_kwargs = {"database": self._database} if self._database else {}
        with self._resolved_driver().session(**session_kwargs) as session:
            session.run(query, parameters)

    def close(self) -> None:
        """Flushes any remaining buffered records, then releases the
        driver's connection pool (only if this transport connected it
        itself — an injected `driver` is left for its owner to close)."""
        super().close()
        if self._injected is None and self._driver is not None:
            self._driver.close()
