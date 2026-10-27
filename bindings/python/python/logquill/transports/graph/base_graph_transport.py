from __future__ import annotations

import json
import uuid
from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, Sequence

from logquill import _native
from logquill.core.records import LogRecord
from logquill.formatters import Formatter
from logquill.transports.batching_transport import BatchingTransport

#: One shared Cypher statement, built once here, that every concrete graph
#: backend executes as-is against its own driver. Written in the portable
#: `FOREACH`-with-`CASE` idiom rather than Neo4j's newer `CALL {}` subquery
#: syntax, since `FOREACH` has been part of Cypher since the beginning and
#: is far more likely to be supported by every openCypher-compatible
#: backend this category covers (including Neptune's), not just Neo4j's own
#: more recent extensions.
CYPHER: str = _native.CYPHER


@dataclass(frozen=True)
class GraphRow:
    """One record, already reduced to exactly what `CYPHER`'s `$rows`
    parameter needs — a plain, JSON-serializable shape every backend's
    driver can pass through its own parameter-binding call unchanged."""

    key: str
    props: dict[str, Any]
    session_key: str | None
    parent_key: str | None


class BaseGraphTransport(BatchingTransport[GraphRow]):
    """Shared base for every graph-database sink: the one piece of logic
    every backend needs is identical regardless of which database it talks
    to — record → node, `parent_span_id` → a `PARENT_OF` edge, `run_id`/
    `trace_id` → a `Session` node every record in that run/trace is linked
    to via `HAS_RECORD` — so that logic lives here, once, as `CYPHER`.
    A concrete backend only has to know how to execute one parameterized
    Cypher/openCypher query against its own driver; see `_execute`.

    **Node identity.** A record that carries its own `span_id` (a span's
    closing record) is keyed by that id — stable and globally unique, so
    re-delivering the same span's record twice merges onto the same node
    rather than duplicating it. A record with no `span_id` of its own (an
    ordinary `.action()`/`.thought()`/... line) has no naturally stable
    identity, so it's keyed by a fresh random id each time — it still
    becomes its own node and still gets a `PARENT_OF` edge from whichever
    span it's nested in, it just isn't idempotent under a retry the way a
    span's own node is.

    **Reconstructing the tree.** Because every node's `PARENT_OF` edges
    point from `meta.parent_span_id` to the record's own key, a Cypher
    traversal from any root (a node with no incoming `PARENT_OF` edge)
    reproduces the exact span hierarchy that was logged — no manual
    graph-writing code at any call site, and switching to a different
    concrete backend never touches a call site either, only which
    transport class is constructed.

    **Session grouping.** `run_key`/`trace_key` name which `meta` fields
    this instance groups records under; `run_key` is tried first, falling
    back to `trace_key` — a record with neither is still its own node, it
    just isn't linked to any `Session`.

    Batched the same way every other sink in this category is: one
    multi-row Cypher statement per flush (`UNWIND $rows`), never one query
    per record.
    """

    def __init__(
        self,
        *,
        formatter: Formatter | None = None,
        max_records: int = 100,
        max_bytes: int = 1_000_000,
        run_key: str = "run_id",
        trace_key: str = "trace_id",
    ) -> None:
        super().__init__(formatter=formatter, max_records=max_records, max_bytes=max_bytes)
        self.run_key = run_key
        self.trace_key = trace_key

    def _to_item(self, formatted: str, record: LogRecord) -> GraphRow:
        row = _native.graph_row(record, self.run_key, self.trace_key)
        key = row["key"]
        if key == _native.LEAF_PLACEHOLDER:
            # A record with no span id of its own has no stable identity: a fresh one each time.
            key = f"leaf-{uuid.uuid4().hex}"
        return GraphRow(
            key=key,
            props=row["props"],
            session_key=row["session_key"],
            parent_key=row["parent_key"],
        )

    def _size_of(self, item: GraphRow) -> int:
        return len(json.dumps(item.props, default=str).encode("utf-8")) + len(item.key) + 32

    def _send_batch(self, batch: Sequence[GraphRow]) -> None:
        rows = [
            {
                "key": row.key,
                "props": row.props,
                "session_key": row.session_key,
                "parent_key": row.parent_key,
            }
            for row in batch
        ]
        self._execute(CYPHER, {"rows": rows})

    @abstractmethod
    def _execute(self, query: str, parameters: dict[str, Any]) -> None:
        """Runs `query` (always `CYPHER`) against this backend's own
        driver with `parameters`. Called only with a non-empty batch; a
        raised exception is caught by `BatchingTransport.flush()` and
        logged, not propagated to the caller."""
        ...
