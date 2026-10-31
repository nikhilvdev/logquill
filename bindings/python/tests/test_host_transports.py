"""The transports that need a driver, run against fake drivers: SQL rows and DDL, and the graph
backends, all of which take their mapping from the Rust core."""

import json

import pytest

from logquill import Logger, RunPlugin
from logquill.transports.graph.falkordb_transport import FalkorDBTransport
from logquill.transports.graph.neo4j_transport import Neo4jTransport
from logquill.transports.graph.neptune_transport import NeptuneTransport
from logquill.transports.sql.mysql_transport import MySQLTransport
from logquill.transports.sql.postgres_transport import PostgresTransport

# ---- SQL --------------------------------------------------------------------------------------


class Cursor:
    def __init__(self, statements):
        self.statements = statements

    def execute(self, sql, parameters=()):
        self.statements.append((sql, list(parameters)))


class Connection:
    def __init__(self):
        self.statements = []
        self.commits = 0
        self.closed = False

    def cursor(self):
        return Cursor(self.statements)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    ("transport_class", "marker", "json_type"),
    [(PostgresTransport, "::jsonb", "JSONB"), (MySQLTransport, "", "JSON")],
)
def test_sql_transports_insert_one_multi_row_statement_with_the_llm_column(
    transport_class, marker, json_type
):
    connection = Connection()
    transport = transport_class(connection=connection, max_records=3, ensure_schema=True)
    log = Logger("app", transports=[transport], async_dispatch=False)
    log.llm_call(model="m", tokens_in=3, run_id="r1")
    log.info("two", run_id="r1", span_id="s1", note="café")
    log.info("three")
    log.close()
    ddl, *inserts = [sql for sql, _ in connection.statements]
    assert f"meta {json_type} NOT NULL, llm {json_type}" in ddl
    assert len(inserts) == 1 and inserts[0].count("(%s") == 3, "one statement for the whole batch"
    assert "(timestamp, level, logger, message, meta, llm, run_id" in inserts[0]
    assert ("::jsonb" in inserts[0]) == (marker == "::jsonb")
    params = connection.statements[-1][1]
    assert len(params) == 30
    first = params[:10]
    assert first[3] == "llm_call" and json.loads(first[5]) == {"model": "m", "tokens_in": 3}
    assert first[6] == "r1"
    second = params[10:20]
    assert second[5] is None and json.loads(second[4])["note"] == "café" and second[7] == "s1"
    assert connection.commits >= 1


def test_sql_transports_refuse_a_table_name_that_is_not_an_identifier():
    for transport_class in (PostgresTransport, MySQLTransport):
        with pytest.raises(ValueError, match="identifier"):
            transport_class(connection=Connection(), table_name="logs; DROP TABLE logs")
    PostgresTransport(connection=Connection(), table_name="audit.logs")


# ---- graph ------------------------------------------------------------------------------------


class Session:
    def __init__(self, owner):
        self.owner = owner

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, parameters):
        self.owner.calls.append((query, parameters))


class Neo4jDriver:
    def __init__(self):
        self.calls = []

    def session(self, **kwargs):
        return Session(self)

    def close(self):
        pass


class FalkorGraph:
    def __init__(self):
        self.calls = []

    def query(self, query, parameters):
        self.calls.append((query, parameters))


class FalkorClient:
    def __init__(self):
        self.graph = FalkorGraph()

    def select_graph(self, name):
        return self.graph

    def close(self):
        pass


class NeptuneClient:
    def __init__(self):
        self.calls = []

    def execute_open_cypher_query(self, openCypherQuery, parameters):
        self.calls.append((openCypherQuery, json.loads(parameters)))


def run_agent(transport):
    log = Logger("agent", transports=[transport], plugins=[RunPlugin(run_id="run-1")], async_dispatch=False)
    with log.span("outer"):
        with log.span("inner"):
            log.action("tool call", tool="web")
    log.close()


def tree_of(rows):
    """Child span -> its parent, from the rows a backend was sent."""
    by_key = {row["key"]: row for row in rows}
    return {
        row["props"]["message"]: by_key[row["parent_key"]]["props"]["message"]
        for row in rows
        if row["parent_key"] in by_key
    }


def test_every_graph_backend_sends_the_same_statement_and_the_same_tree():
    neo, falkor, neptune = Neo4jDriver(), FalkorClient(), NeptuneClient()
    run_agent(Neo4jTransport(driver=neo))
    run_agent(FalkorDBTransport(client=falkor))
    run_agent(NeptuneTransport(client=neptune))
    sent = [neo.calls, falkor.graph.calls, neptune.calls]
    queries = {query for calls in sent for query, _ in calls}
    assert len(queries) == 1 and "PARENT_OF" in queries.pop()
    trees = [tree_of([row for _, params in calls for row in params["rows"]]) for calls in sent]
    assert trees[0] == {"inner": "outer", "tool call": "inner"}
    assert trees[0] == trees[1] == trees[2]
    rows = [row for _, params in neo.calls for row in params["rows"]]
    assert {row["session_key"] for row in rows} == {"run-1"}
    leaf = next(row for row in rows if row["props"]["message"] == "tool call")
    assert leaf["key"].startswith("leaf-") and "span_id" not in leaf["props"]


def test_two_records_without_a_span_id_get_different_node_keys():
    driver = Neo4jDriver()
    log = Logger("agent", transports=[Neo4jTransport(driver=driver, max_records=2)], async_dispatch=False)
    log.info("a")
    log.info("a")
    keys = [row["key"] for _, params in driver.calls for row in params["rows"]]
    assert len(set(keys)) == 2
