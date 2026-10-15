from __future__ import annotations

import json

from logquill import Logger, RunPlugin, TraceContextPlugin
from logquill.tracing.mcp import RUN_ID_KEY, TRACEPARENT_KEY, inbound, propagate
from logquill.transports.transport import CollectingTransport


def test_propagate_carries_a_w3c_traceparent() -> None:
    meta = propagate()

    parts = meta[TRACEPARENT_KEY].split("-")
    assert len(parts) == 4
    assert parts[0] == "00"
    assert len(parts[1]) == 32  # trace id
    assert len(parts[2]) == 16  # span id
    assert RUN_ID_KEY not in meta


def test_propagate_uses_a_given_trace_id() -> None:
    meta = propagate(trace_id="4bf92f3577b34da6a3ce929d0e0e4736")

    assert meta[TRACEPARENT_KEY].split("-")[1] == "4bf92f3577b34da6a3ce929d0e0e4736"


def test_propagate_includes_run_id_only_when_given() -> None:
    assert RUN_ID_KEY not in propagate()
    assert propagate(run_id="run-1")[RUN_ID_KEY] == "run-1"


def test_the_client_server_call_shares_one_trace_id_end_to_end() -> None:
    client_sink = CollectingTransport()
    client_logger = Logger("client", transports=[client_sink], plugins=[TraceContextPlugin()])
    call_record = client_logger.info("calling the search tool")
    assert call_record is not None
    client_trace_id = call_record["meta"]["trace_id"]

    # the client sends `propagate(trace_id=...)`'s result as `meta=` on its MCP
    # request — simulated here as a JSON round trip, since that's what
    # actually crosses the wire
    outbound_meta = json.loads(json.dumps(propagate(trace_id=client_trace_id)))

    server_sink = CollectingTransport()
    server_logger = Logger("server", transports=[server_sink], plugins=[TraceContextPlugin()])
    with inbound(outbound_meta, server="files", tool="search"):
        server_logger.info("handling the call")

    server_trace_id = server_sink.records[0]["meta"]["trace_id"]
    assert outbound_meta[TRACEPARENT_KEY].split("-")[1] == client_trace_id
    assert server_trace_id == client_trace_id


def test_inbound_stamps_mcp_server_and_tool_on_every_record_in_the_block() -> None:
    sink = CollectingTransport()
    logger = Logger("server", transports=[sink])

    with inbound(None, server="files", tool="read_file"):
        logger.info("first")
        logger.warn("second")
    logger.error("outside the block")

    assert sink.records[0]["meta"]["mcp"] == {"server": "files", "tool": "read_file"}
    assert sink.records[1]["meta"]["mcp"] == {"server": "files", "tool": "read_file"}
    assert "mcp" not in sink.records[2]["meta"]


def test_inbound_with_no_meta_at_all_still_stamps_mcp_but_has_no_trace_to_inherit() -> None:
    sink = CollectingTransport()
    logger = Logger("server", transports=[sink], plugins=[TraceContextPlugin()])

    with inbound(None, server="files", tool="search"):
        logger.info("handled")

    assert sink.records[0]["meta"]["mcp"] == {"server": "files", "tool": "search"}
    assert isinstance(sink.records[0]["meta"]["trace_id"], str)  # generated, not inherited


def test_a_client_run_id_lands_under_meta_mcp_and_never_shadows_the_servers_own_run_plugin() -> (
    None
):
    sink = CollectingTransport()
    logger = Logger("server", transports=[sink], plugins=[RunPlugin(run_id="server-run")])
    meta = propagate(run_id="client-run")

    with inbound(meta, server="files", tool="search"):
        logger.info("handled")

    assert sink.records[0]["meta"]["mcp"]["run_id"] == "client-run"
    assert sink.records[0]["meta"]["run_id"] == "server-run"


def test_inbound_restores_the_previous_traceparent_after_the_block() -> None:
    from logquill.plugins.trace_context_plugin import reset_traceparent, set_traceparent

    outer_token = set_traceparent("00-" + "a" * 32 + "-" + "b" * 16 + "-01")
    sink = CollectingTransport()
    logger = Logger("server", transports=[sink], plugins=[TraceContextPlugin()])
    try:
        with inbound(propagate(), server="files", tool="search"):
            pass
        logger.info("after the block")
    finally:
        reset_traceparent(outer_token)

    assert sink.records[0]["meta"]["trace_id"] == "a" * 32


def test_inbound_restores_the_traceparent_even_if_the_block_raises() -> None:
    from logquill.plugins.trace_context_plugin import _current_traceparent

    before = _current_traceparent.get()
    try:
        with inbound(propagate(), server="files", tool="search"):
            raise ValueError("tool failed")
    except ValueError:
        pass

    assert _current_traceparent.get() == before


def test_a_malformed_traceparent_falls_back_to_generating_a_fresh_trace_id() -> None:
    sink = CollectingTransport()
    logger = Logger("server", transports=[sink], plugins=[TraceContextPlugin()])

    with inbound({TRACEPARENT_KEY: "not-a-real-traceparent"}, server="files", tool="search"):
        logger.info("handled")

    assert isinstance(sink.records[0]["meta"]["trace_id"], str)
    assert sink.records[0]["meta"]["trace_id"] != "not-a-real-traceparent"
