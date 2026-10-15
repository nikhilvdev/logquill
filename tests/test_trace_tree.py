from __future__ import annotations

from typing import Any

from logquill import Logger, RunPlugin
from logquill.cli.trace_tree import Rollup, TraceBuilder, build_trace, render_tree
from logquill.transports.transport import CollectingTransport


def _run(run_id: str = "run-1") -> list[dict[str, Any]]:
    sink = CollectingTransport()
    logger = Logger("app.agent", transports=[sink], plugins=[RunPlugin(run_id=run_id)])

    with logger.span("run", operation="invoke_agent", agent_name="planner"):
        logger.thought("plan the work")
        with logger.span("plan_step"):
            logger.llm_call(
                "chat",
                model="m1",
                tokens_in=100,
                tokens_out=20,
                cost_usd=0.01,
                finish_reason="stop",
            )
        logger.action("look it up", tool="search")
        logger.observation("found it", tool="search")
    return sink.records


def test_a_full_run_reconstructs_into_one_root_matching_the_span_nesting() -> None:
    records = _run()

    builder = build_trace(records, "run-1")

    assert builder.record_count == len(records)
    (root,) = builder.roots
    assert root.record["message"] == "run"
    assert root.is_span
    thought, step, action, observation = root.children
    assert thought.record["message"] == "plan the work"
    assert step.record["message"] == "plan_step"
    assert action.record["message"] == "look it up"
    assert observation.record["message"] == "found it"
    (llm_call,) = step.children
    assert llm_call.record["llm"]["model"] == "m1"


def test_records_from_other_runs_are_skipped_and_not_kept() -> None:
    records = _run("run-1") + _run("run-2")

    builder = build_trace(records, "run-1")

    assert builder.record_count == len(_run("run-1"))
    assert all(r.record["meta"]["run_id"] == "run-1" for r in _iter(builder.roots))


def _iter(nodes):  # noqa: ANN001, ANN202
    for node in nodes:
        yield node
        yield from _iter(node.children)


def test_feed_reports_whether_a_record_belonged_to_the_run() -> None:
    records = _run("run-1") + _run("run-2")
    builder = TraceBuilder("run-1")

    kept = [builder.feed(r) for r in records]

    assert kept.count(True) == len(_run("run-1"))
    assert kept.count(False) == len(_run("run-2"))


def test_rollup_sums_tokens_and_cost_from_every_descendant() -> None:
    records = _run()
    builder = build_trace(records, "run-1")

    rollup = builder.roots[0].rollup()

    assert rollup == Rollup(tokens_in=100, tokens_out=20, cost_usd=0.01)


def test_a_span_with_no_llm_descendants_has_a_zero_rollup() -> None:
    sink = CollectingTransport()
    logger = Logger("app", transports=[sink], plugins=[RunPlugin(run_id="run-x")])
    with logger.span("work"):
        logger.info("plain log line")

    builder = build_trace(sink.records, "run-x")

    assert builder.roots[0].rollup() == Rollup()


def test_records_with_no_span_at_all_are_still_roots_in_order() -> None:
    sink = CollectingTransport()
    logger = Logger("app", transports=[sink], plugins=[RunPlugin(run_id="run-x")])
    logger.thought("first")
    logger.decision("second")

    builder = build_trace(sink.records, "run-x")

    assert [r.record["message"] for r in builder.roots] == ["first", "second"]


def test_an_orphaned_span_whose_parent_never_closed_still_surfaces_as_a_root() -> None:
    # simulates a crashed/truncated run: the parent's own closing record is
    # missing, but a record it logged is still on disk
    sink = CollectingTransport()
    logger = Logger("app", transports=[sink], plugins=[RunPlugin(run_id="run-x")])
    with logger.span("outer"):
        logger.info("inside")
    records = [r for r in sink.records if r["message"] != "outer"]  # drop the parent's own record

    builder = build_trace(records, "run-x")

    assert [r.record["message"] for r in builder.roots] == ["inside"]


def test_two_records_sharing_a_span_id_do_not_crash_the_builder() -> None:
    # adversarial/malformed input: two closing records claim the same span_id
    sink = CollectingTransport()
    logger = Logger("app", transports=[sink], plugins=[RunPlugin(run_id="run-x")])
    with logger.span("a", span_id="dupe" * 4):
        pass
    with logger.span("b", span_id="dupe" * 4):
        pass

    builder = build_trace(sink.records, "run-x")  # must not raise

    assert len(builder.roots) == 2


def test_render_tree_shows_duration_tokens_and_cost() -> None:
    builder = build_trace(_run(), "run-1")

    text = render_tree(builder.roots)

    assert "run  (" in text
    assert "100→20 tok" in text
    assert "$0.0100" in text
    assert "├─ " in text and "└─ " in text and "│  " in text


def test_render_tree_applies_the_given_colorize_function() -> None:
    builder = build_trace(_run(), "run-1")

    text = render_tree(builder.roots, colorize=lambda label, level: f"<{level}>{label}</{level}>")

    assert "<INFO>[INFO] run" in text


def test_render_tree_of_an_empty_run_is_an_empty_string() -> None:
    assert render_tree([]) == ""


def test_records_missing_meta_entirely_do_not_crash_feed() -> None:
    builder = TraceBuilder("run-1")

    assert builder.feed({"message": "no meta key at all"}) is False
    assert builder.feed({"meta": None}) is False
