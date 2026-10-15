"""`logquill serve` — a local, offline web UI for browsing traced runs.

Built entirely on the stdlib (`http.server`, `sqlite3`): no new dependency,
and nothing leaves the machine — the whole point of "local-first". Reads
records from a JSONL file, or (`db=True`) the `logs` table a `SQLiteTransport`
wrote. Run summaries are computed once, streaming through the source at
startup, and cached; `/api/trace` and `/api/search` stream through the
source again per request rather than holding it in memory, so a large file
costs request latency, not memory (see `logquill/trace_tree.py`).

One documented gap: a source that's still being appended to (a live capture)
isn't picked up after startup — this is a snapshot of the file/database as
it was when the server started, not a live tail. Restart to pick up new runs.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from logquill.cli.trace_tree import build_trace

_logger = logging.getLogger("logquill")

STATIC_DIR = Path(__file__).parent / "static"

#: `/api/search` never returns more than this many records in one response —
#: a search box is for finding a handful of matches, not paginating a
#: multi-gigabyte file through the browser.
MAX_SEARCH_RESULTS = 500


class RecordSource(Protocol):
    """Where `logquill serve` reads records from. `read_all()` is called
    once per request that needs them — implementations should stream, not
    build a list, so a request costs memory proportional to what it keeps
    (a run, a page of search results), not the whole source."""

    def read_all(self) -> Iterator[dict[str, Any]]:
        """Yields every record, in the order it was written."""
        ...


class JSONLSource:
    """Reads records from a LogQuill JSONL file, one line at a time. A line
    that isn't valid JSON, or isn't a JSON object, is skipped — the same
    tolerance `logquill tail`/`trace` give a log file that isn't perfectly
    clean."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read_all(self) -> Iterator[dict[str, Any]]:
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    yield record


class SQLiteSource:
    """Reads records from the fixed `logs` table schema every SQL transport
    writes (see `BaseSQLTransport`) via a `SQLiteTransport`-created database.

    **Limitation, inherent to that schema**: only `meta`'s `run_id`/`span_id`/
    `parent_span_id`/`trace_id` are stored as queryable columns — an LLM
    call's `llm` block (tokens, cost) is not a column `BaseSQLTransport`
    writes, so runs traced from a SQLite source never show token/cost
    annotations, regardless of what the original records carried. Trace from
    the original JSONL file (or a transport that does keep `llm`) to see them.
    """

    def __init__(self, path: str | Path, *, table: str = "logs") -> None:
        self.path = Path(path)
        self.table = table

    def read_all(self) -> Iterator[dict[str, Any]]:
        connection = sqlite3.connect(str(self.path))
        try:
            cursor = connection.execute(
                f"SELECT timestamp, level, logger, message, meta, run_id, span_id, "  # noqa: S608
                f"parent_span_id, trace_id FROM {self.table} ORDER BY id"
            )
            for row in cursor:
                yield _row_to_record(row)
        finally:
            connection.close()


def _row_to_record(row: tuple[Any, ...]) -> dict[str, Any]:
    timestamp, level, logger_name, message, meta_json, run_id, span_id, parent_span_id, trace_id = (
        row
    )
    try:
        meta = json.loads(meta_json) if meta_json else {}
    except (json.JSONDecodeError, TypeError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    for key, value in (
        ("run_id", run_id),
        ("span_id", span_id),
        ("parent_span_id", parent_span_id),
        ("trace_id", trace_id),
    ):
        if value is not None:
            meta.setdefault(key, value)
    return {
        "schema_version": "1.0",
        "timestamp": timestamp,
        "level": level,
        "logger": logger_name,
        "message": message,
        "meta": meta,
    }


@dataclass
class RunSummary:
    """One row of the run list."""

    run_id: str
    record_count: int = 0
    start: str | None = None
    end: str | None = None
    level_counts: dict[str, int] = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    agent_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "record_count": self.record_count,
            "start": self.start,
            "end": self.end,
            "level_counts": self.level_counts,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": self.cost_usd,
            "agent_name": self.agent_name,
            "error_count": self.level_counts.get("ERROR", 0) + self.level_counts.get("FATAL", 0),
        }


def summarize_runs(records: Iterable[Mapping[str, Any]]) -> list[RunSummary]:
    """One pass over `records`, building one `RunSummary` per distinct
    `meta.run_id` seen — a record with no `run_id` isn't part of any run and
    is left out of the list (it still shows up inside `/api/search`).
    Memory here is proportional to the number of distinct runs, not the
    number of records.
    """
    summaries: dict[str, RunSummary] = {}
    for record in records:
        meta = record.get("meta")
        meta = meta if isinstance(meta, dict) else {}
        run_id = meta.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            continue

        summary = summaries.setdefault(run_id, RunSummary(run_id))
        summary.record_count += 1
        timestamp = record.get("timestamp")
        if isinstance(timestamp, str):
            if summary.start is None or timestamp < summary.start:
                summary.start = timestamp
            if summary.end is None or timestamp > summary.end:
                summary.end = timestamp
        level = record.get("level")
        if isinstance(level, str):
            summary.level_counts[level] = summary.level_counts.get(level, 0) + 1
        llm = record.get("llm")
        if isinstance(llm, dict):
            if isinstance(llm.get("tokens_in"), int):
                summary.tokens_in += llm["tokens_in"]
            if isinstance(llm.get("tokens_out"), int):
                summary.tokens_out += llm["tokens_out"]
            if isinstance(llm.get("cost_usd"), (int, float)):
                summary.cost_usd += llm["cost_usd"]
        if meta.get("operation") == "invoke_agent" and isinstance(meta.get("agent_name"), str):
            summary.agent_name = meta["agent_name"]

    return sorted(summaries.values(), key=lambda s: s.start or "", reverse=True)


def _matches_search(record: Mapping[str, Any], query: str) -> bool:
    if not query:
        return True
    haystack = f"{record.get('message', '')} {json.dumps(record.get('meta') or {}, default=str)}"
    return query in haystack.lower()


def search_records(
    records: Iterable[Mapping[str, Any]],
    *,
    query: str = "",
    level: str | None = None,
    run_id: str | None = None,
    limit: int = MAX_SEARCH_RESULTS,
) -> list[dict[str, Any]]:
    """Streams through `records`, keeping at most `limit` that match every
    given filter — a substring of `query` in the message or `meta`
    (case-insensitive), an exact `level`, and/or an exact `meta.run_id`."""
    query = query.lower()
    results: list[dict[str, Any]] = []
    for record in records:
        if level is not None and record.get("level") != level:
            continue
        if run_id is not None and (record.get("meta") or {}).get("run_id") != run_id:
            continue
        if not _matches_search(record, query):
            continue
        results.append(dict(record))
        if len(results) >= limit:
            break
    return results


class TraceViewerServer:
    """The HTTP server behind `logquill serve`: one static page plus a small
    JSON API, all reading from one `RecordSource`.

    Run summaries are computed once, at construction, by streaming through
    the source — see the module docstring for what that means for a source
    that's still growing.
    """

    def __init__(self, source: RecordSource, *, host: str = "127.0.0.1", port: int = 0) -> None:
        self.source = source
        self.runs = summarize_runs(source.read_all())
        self._host = host
        handler = _make_handler(self)
        self._httpd = ThreadingHTTPServer((host, port), handler)

    @property
    def port(self) -> int:
        """The port actually bound — resolved even when constructed with
        `port=0` (let the OS pick one)."""
        return int(self._httpd.server_address[1])

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self.port}/"

    def serve_forever(self) -> None:
        """Blocks, serving requests until `shutdown()` is called (typically
        from a signal handler or another thread)."""
        self._httpd.serve_forever()

    def shutdown(self) -> None:
        """Stops `serve_forever()` and releases the listening socket. Safe
        to call from a different thread than the one running `serve_forever()`."""
        self._httpd.shutdown()
        self._httpd.server_close()


def _make_handler(server: TraceViewerServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            _logger.debug("logquill serve: " + format, *args)

        def do_GET(self) -> None:  # noqa: N802
            _route(self, server)

    return Handler


def _send_json(handler: BaseHTTPRequestHandler, payload: Any, *, status: int = 200) -> None:
    body = json.dumps(payload, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_file(handler: BaseHTTPRequestHandler, path: Path, content_type: str) -> None:
    try:
        body = path.read_bytes()
    except OSError:
        handler.send_response(404)
        handler.end_headers()
        return
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _route(handler: BaseHTTPRequestHandler, server: TraceViewerServer) -> None:
    parsed = urlsplit(handler.path)
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}

    if parsed.path == "/":
        _send_file(handler, STATIC_DIR / "trace_viewer.html", "text/html; charset=utf-8")
    elif parsed.path == "/api/runs":
        _send_json(handler, [run.to_dict() for run in server.runs])
    elif parsed.path == "/api/trace":
        run_id = query.get("run_id")
        if not run_id:
            _send_json(handler, {"error": "run_id is required"}, status=400)
            return
        builder = build_trace(server.source.read_all(), run_id)
        _send_json(handler, [node.to_dict() for node in builder.roots])
    elif parsed.path == "/api/search":
        results = search_records(
            server.source.read_all(),
            query=query.get("q", ""),
            level=query.get("level") or None,
            run_id=query.get("run_id") or None,
            limit=int(query.get("limit", MAX_SEARCH_RESULTS)),
        )
        _send_json(handler, results)
    else:
        handler.send_response(404)
        handler.end_headers()
