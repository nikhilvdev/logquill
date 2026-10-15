from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

import pytest

from logquill import Logger, RunPlugin
from logquill.cli.webui import (
    STATIC_DIR,
    JSONLSource,
    SQLiteSource,
    TraceViewerServer,
    search_records,
    summarize_runs,
)
from logquill.transports.file_transport import FileTransport
from logquill.transports.sql.sqlite_transport import SQLiteTransport


def _write_a_run(path: Path, run_id: str = "run-1") -> None:
    logger = Logger(
        "app.agent", transports=[FileTransport(path)], plugins=[RunPlugin(run_id=run_id)]
    )
    with logger.span("run", operation="invoke_agent", agent_name="planner"):
        logger.thought("plan")
        with logger.span("step"):
            logger.llm_call("chat", model="m", tokens_in=10, tokens_out=5, cost_usd=0.02)
        logger.action("call", tool="search")
    logger.error("something broke")
    logger.close()


# --- JSONLSource -----------------------------------------------------------


def test_jsonl_source_reads_every_record_in_order(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    _write_a_run(path)

    records = list(JSONLSource(path).read_all())

    # file order = write order: a span closes *after* what's nested inside it
    assert [r["message"] for r in records] == [
        "plan",
        "chat",
        "step",
        "call",
        "run",
        "something broke",
    ]


def test_jsonl_source_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_text(
        '{"message": "a", "meta": {}}\nnot json\n["not", "an", "object"]\n', encoding="utf-8"
    )

    records = list(JSONLSource(path).read_all())

    assert [r["message"] for r in records] == ["a"]


# --- SQLiteSource ------------------------------------------------------------


def test_sqlite_source_reconstructs_records_with_ids_from_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "logs.sqlite"
    transport = SQLiteTransport(filename=str(db_path), ensure_schema=True, max_records=1)
    logger = Logger("app", transports=[transport], plugins=[RunPlugin(run_id="run-1")])
    logger.info("hello", user_id=42)
    logger.close()

    records = list(SQLiteSource(db_path).read_all())

    assert len(records) == 1
    assert records[0]["message"] == "hello"
    assert records[0]["meta"]["user_id"] == 42
    assert records[0]["meta"]["run_id"] == "run-1"


def test_sqlite_source_has_no_llm_block_even_if_meta_has_llm_looking_keys(tmp_path: Path) -> None:
    db_path = tmp_path / "logs.sqlite"
    transport = SQLiteTransport(filename=str(db_path), ensure_schema=True, max_records=1)
    logger = Logger("app", transports=[transport], plugins=[RunPlugin(run_id="run-1")])
    logger.llm_call("chat", model="m", tokens_in=5)
    logger.close()

    (record,) = list(SQLiteSource(db_path).read_all())

    assert "llm" not in record  # BaseSQLTransport's schema doesn't carry it


def test_sqlite_source_handles_a_null_meta_column(tmp_path: Path) -> None:
    db_path = tmp_path / "logs.sqlite"
    connection = sqlite3.connect(str(db_path))
    connection.execute(
        "CREATE TABLE logs (id INTEGER PRIMARY KEY, timestamp TEXT, level TEXT, logger TEXT, "
        "message TEXT, meta TEXT, run_id TEXT, span_id TEXT, parent_span_id TEXT, trace_id TEXT)"
    )
    connection.execute(
        "INSERT INTO logs (timestamp, level, logger, message, meta, run_id) VALUES (?,?,?,?,?,?)",
        ("t", "INFO", "app", "hi", None, "run-1"),
    )
    connection.commit()
    connection.close()

    (record,) = list(SQLiteSource(db_path).read_all())

    assert record["meta"] == {"run_id": "run-1"}


# --- summarize_runs / search_records -----------------------------------------


def test_summarize_runs_aggregates_tokens_cost_levels_and_agent_name(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    _write_a_run(path)

    (summary,) = summarize_runs(JSONLSource(path).read_all())

    assert summary.run_id == "run-1"
    assert summary.record_count == 6
    assert summary.tokens_in == 10
    assert summary.tokens_out == 5
    assert summary.cost_usd == 0.02
    assert summary.agent_name == "planner"
    assert summary.level_counts == {"INFO": 5, "ERROR": 1}
    assert summary.to_dict()["error_count"] == 1


def test_summarize_runs_skips_records_with_no_run_id() -> None:
    records: list[dict[str, Any]] = [
        {"message": "loose", "meta": {}, "level": "INFO", "timestamp": "t"}
    ]

    assert summarize_runs(records) == []


def test_summarize_runs_orders_by_start_time_newest_first() -> None:
    records = [
        {
            "message": "a",
            "level": "INFO",
            "timestamp": "2026-01-01T00:00:00.000Z",
            "meta": {"run_id": "old"},
        },
        {
            "message": "b",
            "level": "INFO",
            "timestamp": "2026-01-02T00:00:00.000Z",
            "meta": {"run_id": "new"},
        },
    ]

    runs = summarize_runs(records)

    assert [r.run_id for r in runs] == ["new", "old"]


def test_search_records_filters_by_query_level_and_run_id() -> None:
    records = [
        {"message": "search failed", "level": "ERROR", "meta": {"run_id": "a"}},
        {"message": "search ok", "level": "INFO", "meta": {"run_id": "a"}},
        {"message": "search failed", "level": "ERROR", "meta": {"run_id": "b"}},
        {"message": "unrelated", "level": "ERROR", "meta": {"run_id": "a"}},
    ]

    assert len(search_records(records, query="search")) == 3
    assert len(search_records(records, query="search", level="ERROR")) == 2
    assert len(search_records(records, query="search", run_id="b")) == 1


def test_search_records_matches_inside_meta_too() -> None:
    records = [{"message": "x", "level": "INFO", "meta": {"user_id": "customer-42"}}]

    assert len(search_records(records, query="customer-42")) == 1
    assert len(search_records(records, query="nope")) == 0


def test_search_records_is_capped_at_limit() -> None:
    records = [{"message": f"m{i}", "level": "INFO", "meta": {}} for i in range(50)]

    assert len(search_records(records, limit=5)) == 5


def test_search_records_with_no_filters_returns_everything_up_to_the_cap() -> None:
    records = [{"message": "a", "level": "INFO", "meta": {}}]

    assert search_records(records) == records


# --- the real HTTP server ----------------------------------------------------


@pytest.fixture()
def running_server(tmp_path: Path) -> Iterator[TraceViewerServer]:
    path = tmp_path / "app.log"
    _write_a_run(path)
    server = TraceViewerServer(JSONLSource(path), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(server.url, timeout=1)
                break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.02)
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _get(server: TraceViewerServer, path: str) -> Any:
    with urllib.request.urlopen(server.url.rstrip("/") + path, timeout=5) as response:
        return response.status, json.loads(response.read())


def test_root_serves_the_static_page(running_server: TraceViewerServer) -> None:
    with urllib.request.urlopen(running_server.url, timeout=5) as response:
        assert response.status == 200
        assert response.headers["Content-Type"].startswith("text/html")
        body = response.read().decode("utf-8")

    assert "<title>LogQuill trace viewer</title>" in body
    assert "/api/runs" in body  # the page really does call the API


def test_api_runs_lists_the_run(running_server: TraceViewerServer) -> None:
    status, runs = _get(running_server, "/api/runs")

    assert status == 200
    assert [r["run_id"] for r in runs] == ["run-1"]


def test_api_trace_returns_the_span_tree(running_server: TraceViewerServer) -> None:
    status, tree = _get(running_server, "/api/trace?run_id=run-1")

    assert status == 200
    assert tree[0]["record"]["message"] == "run"
    assert tree[0]["rollup"]["tokens_in"] == 10


def test_api_trace_without_run_id_is_a_400(running_server: TraceViewerServer) -> None:
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(running_server.url + "api/trace", timeout=5)

    assert exc_info.value.code == 400


def test_api_search_finds_the_error(running_server: TraceViewerServer) -> None:
    status, hits = _get(running_server, "/api/search?q=broke")

    assert status == 200
    assert hits[0]["message"] == "something broke"


def test_an_unknown_path_is_a_404(running_server: TraceViewerServer) -> None:
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(running_server.url + "nope", timeout=5)

    assert exc_info.value.code == 404


def test_port_zero_resolves_to_a_real_bound_port(running_server: TraceViewerServer) -> None:
    assert running_server.port > 0
    assert str(running_server.port) in running_server.url


def test_the_static_page_ships_inside_the_package() -> None:
    assert (STATIC_DIR / "trace_viewer.html").is_file()
