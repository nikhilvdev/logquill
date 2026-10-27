"""Syslog, SQLite, Elasticsearch and OpenTelemetry transports, done in Rust, against real local
sockets, files and servers."""

import json
import socket
import sqlite3

import pytest

import logquill
from logquill import Logger
from logquill.transports.cloud.elasticsearch_transport import ElasticsearchTransport
from logquill.transports.cloud.syslog_transport import SyslogTransport
from logquill.transports.otel import _env
from logquill.transports.otel.logs_transport import OTelLogsTransport
from logquill.transports.otel.otlp_transport import OTLPTransport
from logquill.transports.sql.sqlite_transport import SQLiteTransport

# ---- syslog -----------------------------------------------------------------------------------


def test_syslog_udp_sends_one_rfc5424_datagram_per_record():
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(5)
    port = receiver.getsockname()[1]
    log = Logger("svc.api", transports=[SyslogTransport(host="127.0.0.1", port=port)])
    log.error("disk full", device="sda")
    log.warn("careful")
    messages = [receiver.recv(65535).decode() for _ in range(2)]
    log.close()
    # <facility 1 * 8 + severity> VERSION TIMESTAMP HOST APP PID - - MESSAGE
    first = messages[0].split(" ", 7)
    assert first[0] == "<11>1" and first[3] == "svc.api" and first[5:7] == ["-", "-"]
    assert json.loads(messages[0].split(" - - ", 1)[1])["message"] == "disk full"
    assert messages[1].startswith("<12>1 ")


def test_syslog_tcp_frames_each_message_with_a_newline():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(5)
    port = server.getsockname()[1]
    log = Logger("app", transports=[SyslogTransport(host="127.0.0.1", port=port, protocol="tcp", app_name="myapp")])
    log.info("one")
    log.info("two")
    connection, _ = server.accept()
    connection.settimeout(5)
    data = b""
    while data.count(b"\n") < 2:
        data += connection.recv(4096)
    log.close()
    lines = data.decode().splitlines()
    assert len(lines) == 2 and all(" myapp " in line for line in lines)


def test_syslog_refuses_an_unknown_protocol():
    with pytest.raises(ValueError, match="protocol"):
        SyslogTransport(protocol="carrier-pigeon")


def test_syslog_with_a_custom_sender_keeps_the_python_path():
    sent = []
    transport = SyslogTransport(sender=sent.append)
    assert transport._native is None
    Logger("app", transports=[transport], async_dispatch=False).info("hi")
    assert sent and sent[0].startswith(b"<14>1 ")


# ---- sqlite -----------------------------------------------------------------------------------


def test_sqlite_writes_batches_to_a_file_and_the_llm_block_is_kept(tmp_path):
    path = tmp_path / "logs.db"
    transport = SQLiteTransport(filename=str(path), ensure_schema=True, max_records=2)
    log = Logger("app", transports=[transport])
    with log.span("run", run_id="r1"):
        log.llm_call(model="m", tokens_in=3, tokens_out=4, run_id="r1")
        log.info("plain", run_id="r1", note="café")
    log.close()
    rows = sqlite3.connect(path).execute(
        "SELECT level, message, run_id, span_id, parent_span_id, llm, meta FROM logs ORDER BY id"
    ).fetchall()
    assert [r[1] for r in rows] == ["llm_call", "plain", "run"]
    assert {r[2] for r in rows} == {"r1"}
    assert json.loads(rows[0][5]) == {"model": "m", "tokens_in": 3, "tokens_out": 4}
    assert rows[1][5] is None and json.loads(rows[1][6])["note"] == "café"
    assert rows[0][4] == rows[2][3], "the call is nested under the span"


def test_sqlite_uses_write_ahead_logging_so_a_reader_is_not_blocked(tmp_path):
    path = tmp_path / "wal.db"
    log = Logger("app", transports=[SQLiteTransport(filename=str(path), ensure_schema=True, max_records=1)])
    log.info("one")
    log.flush(5)
    reader = sqlite3.connect(path)
    assert reader.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert reader.execute("SELECT count(*) FROM logs").fetchone()[0] == 1
    log.info("two")
    log.flush(5)
    assert reader.execute("SELECT count(*) FROM logs").fetchone()[0] == 2
    log.close()


def test_sqlite_refuses_a_table_name_that_is_not_an_identifier(tmp_path):
    with pytest.raises(ValueError, match="identifier"):
        SQLiteTransport(filename=str(tmp_path / "x.db"), table_name="logs; DROP TABLE logs")


def test_sqlite_with_a_connection_you_supply_keeps_the_python_path():
    connection = sqlite3.connect(":memory:")
    transport = SQLiteTransport(connection=connection, ensure_schema=True, max_records=1)
    assert transport._native is None
    Logger("app", transports=[transport], async_dispatch=False).info("hi")
    assert connection.execute("SELECT message FROM logs").fetchall() == [("hi",)]


# ---- elasticsearch ----------------------------------------------------------------------------


def test_elasticsearch_posts_bulk_ecs_documents(recorder):
    log = Logger("app", transports=[ElasticsearchTransport(url=recorder.base, index="app-logs", max_records=2)])
    log.info("a", trace_id="t1", span_id="s1")
    log.error("b", stack="Traceback\nValueError: boom")
    log.close()
    assert [r["path"] for r in recorder.requests] == ["/_bulk"]
    lines = [json.loads(line) for line in recorder.bodies()[0].splitlines()]
    assert lines[0] == {"index": {"_index": "app-logs"}} and lines[2] == lines[0]
    first, second = lines[1], lines[3]
    assert first["message"] == "a" and first["log"]["level"] == "info" and "@timestamp" in first
    assert first["trace"]["id"] == "t1" and first["span"]["id"] == "s1"
    assert second["error"]["stack_trace"].endswith("boom") and second["log"]["level"] == "error"


# ---- opentelemetry ----------------------------------------------------------------------------


def attributes(otlp_span):
    return {a["key"]: next(iter(a["value"].values())) for a in otlp_span["attributes"]}


def test_otlp_traces_export_llm_calls_tools_and_spans_as_spans(recorder):
    transport = OTLPTransport(endpoint=f"{recorder.base}/v1/traces", service_name="agent", processor="simple")
    log = Logger("app", transports=[transport], plugins=[logquill.RunPlugin(run_id="r1")])
    with log.span("run", agent_name="helper", operation="invoke_agent"):
        log.llm_call(model="m-1", tokens_in=7, tokens_out=3, finish_reason="stop", provider="acme")
        log.action("search", tool="web")
        log.info("ordinary log lines are not spans")
    log.close()
    document = json.loads("".join(recorder.bodies()[:1]))
    spans = [s for body in recorder.bodies() for rs in json.loads(body)["resourceSpans"] for ss in rs["scopeSpans"] for s in ss["spans"]]
    names = sorted(s["name"] for s in spans)
    assert names == ["chat m-1", "execute_tool web", "invoke_agent helper"]
    llm = next(s for s in spans if s["name"].startswith("chat"))
    attrs = attributes(llm)
    assert attrs["gen_ai.usage.input_tokens"] == "7" and attrs["gen_ai.response.finish_reasons"] is not None
    resource = document["resourceSpans"][0]["resource"]["attributes"]
    assert {"key": "service.name", "value": {"stringValue": "agent"}} in resource
    agent = next(s for s in spans if s["name"].startswith("invoke_agent"))
    assert llm["parentSpanId"] == agent["spanId"] and llm["traceId"] == agent["traceId"]
    assert len(agent["traceId"]) == 32 and len(agent["spanId"]) == 16


def test_otlp_prompt_text_is_exported_only_when_capture_is_on_and_the_policy_allows(recorder):
    def export(capture, policy):
        recorder.requests.clear()
        transport = OTLPTransport(endpoint=f"{recorder.base}/v1/traces", processor="simple", capture_content=capture)
        log = Logger("app", transports=[transport], content_policy=policy)
        log.llm_call(model="m", input_messages=[{"role": "user", "content": "the secret prompt"}])
        log.close()
        return "".join(recorder.bodies())

    assert "the secret prompt" not in export(False, "full")
    assert "the secret prompt" in export(True, "full")
    assert "the secret prompt" not in export(True, "off"), "the content policy still applies first"


def test_otlp_logs_send_every_record_with_severity_and_ids(recorder):
    transport = OTelLogsTransport(endpoint=f"{recorder.base}/v1/logs", processor="simple")
    log = Logger("svc", transports=[transport])
    log.warn("careful", user="u1", trace_id="a" * 32, span_id="b" * 16)
    log.close()
    logs = [r for body in recorder.bodies() for rl in json.loads(body)["resourceLogs"] for sl in rl["scopeLogs"] for r in sl["logRecords"]]
    assert len(logs) == 1
    entry = logs[0]
    assert entry["severityText"] == "WARN" and entry["body"] == {"stringValue": "careful"}
    assert entry["traceId"] == "a" * 32 and entry["spanId"] == "b" * 16
    assert attributes(entry)["user"] == "u1"


def test_otlp_unreachable_collector_never_raises(caplog):
    transport = OTLPTransport(endpoint="http://127.0.0.1:1/v1/traces", processor="simple", timeout=0.5)
    log = Logger("app", transports=[transport])
    log.llm_call(model="m")
    assert log.flush(10) is True
    log.close()


def test_the_endpoint_and_headers_follow_the_standard_environment_variables(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    assert _env.endpoint(None, "traces") is None
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318/")
    assert _env.endpoint(None, "traces") == "http://collector:4318/v1/traces"
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://traces:1/x")
    assert _env.endpoint(None, "traces") == "http://traces:1/x"
    assert _env.endpoint("http://explicit/", "traces") == "http://explicit/"
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "api-key=abc%20def,other=1")
    assert dict(_env.headers({"other": "2"}, "traces")) == {"api-key": "abc def", "other": "2"}


def test_a_bad_semconv_version_and_processor_are_refused():
    with pytest.raises(ValueError, match="semconv"):
        OTLPTransport(semconv_version="v9")
    with pytest.raises(ValueError, match="processor"):
        OTLPTransport(processor="eventually")


def test_the_native_cloud_transports_construct_and_validate_without_touching_the_network():
    from logquill.transports.cloud.app_insights_transport import AppInsightsTransport
    from logquill.transports.cloud.datadog_transport import DatadogTransport
    from logquill.transports.cloud.new_relic_transport import NewRelicTransport

    for transport in (
        DatadogTransport(api_key="k", site="datadoghq.eu"),
        NewRelicTransport(license_key="k", region="EU"),
        AppInsightsTransport(instrumentation_key="k"),
    ):
        assert transport._native is not None and transport._native_dispatch is not None
        transport.close()  # nothing was written, so nothing is sent
    with pytest.raises(Exception, match="region"):
        NewRelicTransport(license_key="k", region="MOON")
    assert logquill.generate_fernet_key()
