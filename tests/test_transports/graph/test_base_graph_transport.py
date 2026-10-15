from __future__ import annotations

from collections import defaultdict
from typing import Any

from logquill import Logger, RunPlugin
from logquill.transports.graph.base_graph_transport import CYPHER, BaseGraphTransport


class FakeGraphTransport(BaseGraphTransport):
    """Records every Cypher call it would have executed, instead of
    talking to a real backend."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _execute(self, query: str, parameters: dict[str, Any]) -> None:
        self.calls.append((query, parameters))


class MiniGraph:
    """A tiny in-memory openCypher-shaped engine for test purposes: applies
    exactly the semantics `CYPHER` describes (MERGE a node by key, link it
    to a session and/or a parent) so a real traversal can be checked
    against, without needing a live database."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.parent_of: dict[str, str] = {}  # child_key -> parent_key
        self.session_of: defaultdict[str, set[str]] = defaultdict(set)

    def apply(self, query: str, parameters: dict[str, Any]) -> None:
        assert query == CYPHER
        for row in parameters["rows"]:
            self.nodes[row["key"]] = row["props"]
            if row["session_key"] is not None:
                self.session_of[row["session_key"]].add(row["key"])
            if row["parent_key"] is not None:
                self.parent_of[row["key"]] = row["parent_key"]

    def children_of(self, key: str) -> list[str]:
        return [child for child, parent in self.parent_of.items() if parent == key]

    def roots(self) -> list[str]:
        return [key for key in self.nodes if key not in self.parent_of]

    def reconstruct_tree(self, key: str) -> dict[str, Any]:
        """A traversal: walk PARENT_OF from `key`, the same thing a Cypher
        `MATCH (n {key: $key})-[:PARENT_OF*0..]->(d) RETURN d` would do."""
        return {
            "message": self.nodes[key]["message"],
            "children": sorted(
                (self.reconstruct_tree(child) for child in self.children_of(key)),
                key=lambda node: node["message"],
            ),
        }


def _run_agent(logger: Logger) -> None:
    with logger.span("run", operation="invoke_agent", agent_name="planner"):
        logger.thought("plan")
        with logger.span("step"):
            logger.llm_call("chat", model="m", tokens_in=10, tokens_out=5, cost_usd=0.01)
        logger.action("call", tool="search")


def test_every_record_becomes_a_row_with_the_shared_cypher_query() -> None:
    sink = FakeGraphTransport(max_records=100)
    logger = Logger("app.agent", transports=[sink], plugins=[RunPlugin(run_id="run-1")])
    _run_agent(logger)
    logger.close()

    ((query, parameters),) = sink.calls
    assert query == CYPHER
    assert len(parameters["rows"]) == 5  # plan, chat, step, call, run (closed together on close())


def test_a_span_is_keyed_by_its_own_span_id_a_leaf_is_keyed_randomly() -> None:
    sink = FakeGraphTransport(max_records=100)
    logger = Logger("app.agent", transports=[sink])
    with logger.span("work", span_id="a" * 16):
        logger.info("leaf")
    logger.close()

    ((query, parameters),) = sink.calls
    leaf, root = parameters["rows"]
    assert root["key"] == "a" * 16
    assert leaf["key"] != "a" * 16
    assert leaf["key"].startswith("leaf-")


def test_a_root_span_has_no_parent_key() -> None:
    sink = FakeGraphTransport(max_records=100)
    logger = Logger("app.agent", transports=[sink])
    with logger.span("root"):
        pass
    logger.close()

    ((query, parameters),) = sink.calls
    (row,) = parameters["rows"]
    assert row["parent_key"] is None


def test_run_id_is_preferred_over_trace_id_for_session_grouping() -> None:
    sink = FakeGraphTransport(max_records=100)
    logger = Logger("app.agent", transports=[sink])
    logger.info("x", run_id="run-1", trace_id="trace-1")
    logger.close()

    ((query, parameters),) = sink.calls
    (row,) = parameters["rows"]
    assert row["session_key"] == "run-1"


def test_trace_id_is_used_when_there_is_no_run_id() -> None:
    sink = FakeGraphTransport(max_records=100)
    logger = Logger("app.agent", transports=[sink])
    logger.info("x", trace_id="trace-1")
    logger.close()

    ((query, parameters),) = sink.calls
    (row,) = parameters["rows"]
    assert row["session_key"] == "trace-1"


def test_a_record_with_neither_has_no_session_key() -> None:
    sink = FakeGraphTransport(max_records=100)
    logger = Logger("app.agent", transports=[sink])
    logger.info("loose")
    logger.close()

    ((query, parameters),) = sink.calls
    (row,) = parameters["rows"]
    assert row["session_key"] is None


def test_meta_is_json_encoded_and_key_fields_are_lifted_onto_props() -> None:
    sink = FakeGraphTransport(max_records=100)
    logger = Logger("app.agent", transports=[sink])
    logger.info("x", run_id="run-1", user_id=42)
    logger.close()

    ((query, parameters),) = sink.calls
    (row,) = parameters["rows"]
    assert row["props"]["run_id"] == "run-1"
    assert '"user_id":42' in row["props"]["meta"]


def test_a_cypher_traversal_reproduces_the_exact_span_tree() -> None:
    """The exit criterion: an agent run logged normally produces a
    queryable node/edge tree reconstructing the exact span hierarchy via a
    Cypher traversal, with no manual graph-writing code at any call site.
    """
    sink = FakeGraphTransport(max_records=100)
    logger = Logger("app.agent", transports=[sink], plugins=[RunPlugin(run_id="run-1")])
    _run_agent(logger)
    logger.close()

    graph = MiniGraph()
    for query, parameters in sink.calls:
        graph.apply(query, parameters)

    (root_key,) = graph.roots()
    tree = graph.reconstruct_tree(root_key)
    assert tree == {
        "message": "run",
        "children": [
            {"message": "call", "children": []},
            {"message": "plan", "children": []},
            {"message": "step", "children": [{"message": "chat", "children": []}]},
        ],
    }
    assert graph.session_of["run-1"] == set(graph.nodes)  # every node is in the run's session


def test_buffering_is_batched_not_one_query_per_record() -> None:
    sink = FakeGraphTransport(max_records=1000)
    logger = Logger("app.agent", transports=[sink])
    for i in range(50):
        logger.info(f"step {i}")
    logger.close()

    assert len(sink.calls) == 1
    assert len(sink.calls[0][1]["rows"]) == 50
