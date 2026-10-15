"""End-to-end tracing tests:

1. A full agent run (5+ steps, at least one nested span) reconstructs exact
   order and nesting when sorted by `meta.span_id`/`meta.parent_span_id`.
2. A synthetic multi-service call chain shares one `trace_id` end to end.

A `LangChainAdapter`-instrumented chain reconstructing its exact call tree
with zero manual instrumentation is covered separately in
`tests/test_adapters/test_langchain_adapter.py`, since it needs the
optional-dependency faking setup that lives there.
"""

from logquill.core.logger import Logger
from logquill.plugins.run_plugin import RunPlugin
from logquill.plugins.trace_context_plugin import (
    TraceContextPlugin,
    reset_traceparent,
    set_traceparent,
)
from logquill.transports.transport import CollectingTransport


def test_five_step_run_with_a_nested_span_reconstructs_order_and_nesting() -> None:
    sink = CollectingTransport()
    logger = Logger("agent", transports=[sink], plugins=[RunPlugin(run_id="run-1")])

    logger.thought("deciding what to do")  # step 0
    logger.action("call search tool")  # step 1
    with logger.span("call_llm") as llm_span:  # closes as step 3
        logger.observation("tool result received")  # step 2, nested under the span
    logger.decision("final answer ready")  # step 4

    assert len(sink.records) == 5

    by_run_id = {r["meta"]["run_id"] for r in sink.records}
    assert by_run_id == {"run-1"}

    # steps are strictly increasing, in call order — recovers "exact order"
    steps = [r["meta"]["step"] for r in sink.records]
    assert steps == sorted(steps) == [0, 1, 2, 3, 4]

    thought, action, observation, span_close, decision = sink.records
    assert thought["meta"]["kind"] == "thought"
    assert action["meta"]["kind"] == "action"
    assert decision["meta"]["kind"] == "decision"

    # the nested call is a child of the span — recovers "nesting"
    assert observation["meta"]["parent_span_id"] == llm_span._span_id
    assert span_close["meta"]["span_id"] == llm_span._span_id
    assert span_close["meta"]["kind"] == "span"

    # sorting by (parent_span_id grouping under span_id) is enough to
    # rebuild the tree: exactly one record's parent_span_id points at the
    # span, and nothing points at a span_id that doesn't exist in the run.
    span_ids = {r["meta"]["span_id"] for r in sink.records if "span_id" in r["meta"]}
    parent_span_ids = {
        r["meta"]["parent_span_id"] for r in sink.records if "parent_span_id" in r["meta"]
    }
    assert parent_span_ids <= span_ids


def test_synthetic_multi_service_call_chain_shares_one_trace_id() -> None:
    # Three independent "services" (separate Logger instances, separate
    # TraceContextPlugin instances — nothing shared but the propagated
    # header), simulating a request hopping across process boundaries.
    edge_sink = CollectingTransport()
    edge_service = Logger("edge", transports=[edge_sink], plugins=[TraceContextPlugin()])

    # The edge service is the one place a trace id is minted (no inbound
    # header yet) — everything downstream propagates it forward explicitly,
    # the way a real inbound request would (headers over the wire), not by
    # sharing Python state.
    edge_record = edge_service.info("received request")
    assert edge_record is not None
    trace_id = edge_record["meta"]["trace_id"]
    traceparent = f"00-{trace_id}-0000000000000001-01"

    auth_sink = CollectingTransport()
    auth_token = set_traceparent(traceparent)
    try:
        auth_service = Logger("auth", transports=[auth_sink], plugins=[TraceContextPlugin()])
        auth_service.info("validated token")
    finally:
        reset_traceparent(auth_token)

    billing_sink = CollectingTransport()
    billing_token = set_traceparent(traceparent)
    try:
        billing_service = Logger(
            "billing", transports=[billing_sink], plugins=[TraceContextPlugin()]
        )
        billing_service.info("charged card")
    finally:
        reset_traceparent(billing_token)

    assert edge_sink.records[0]["meta"]["trace_id"] == trace_id
    assert auth_sink.records[0]["meta"]["trace_id"] == trace_id
    assert billing_sink.records[0]["meta"]["trace_id"] == trace_id
    assert auth_sink.records[0]["logger"] == "auth"
    assert billing_sink.records[0]["logger"] == "billing"
