from __future__ import annotations

import pytest

from logquill import Logger
from logquill.tracing.retry import RetryTracker
from logquill.transports.transport import CollectingTransport


def test_the_first_attempt_is_zero_and_each_reopening_counts_up() -> None:
    tracker = RetryTracker()

    assert [tracker.opened(None, "search") for _ in range(3)] == [0, 1, 2]


def test_a_success_ends_the_chain() -> None:
    tracker = RetryTracker()
    tracker.opened(None, "search")
    tracker.opened(None, "search")

    tracker.succeeded(None, "search")

    assert tracker.opened(None, "search") == 0


def test_calls_are_told_apart_by_span_tool_and_call_id() -> None:
    tracker = RetryTracker()

    assert tracker.opened("s1", "search") == 0
    assert tracker.opened("s2", "search") == 0
    assert tracker.opened("s1", "fetch") == 0
    assert tracker.opened("s1", "search", "c1") == 0
    assert tracker.opened("s1", "search") == 1


def test_the_tracker_is_bounded_and_forgets_the_oldest_calls() -> None:
    tracker = RetryTracker(max_entries=3)

    for i in range(10):
        tracker.opened(None, f"tool-{i}")

    assert len(tracker._attempts) == 3
    assert tracker.opened(None, "tool-0") == 0  # long forgotten, so not a retry


def test_max_entries_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_entries"):
        RetryTracker(max_entries=0)


def _logger() -> tuple[Logger, CollectingTransport]:
    sink = CollectingTransport()
    return Logger("app.agent", transports=[sink]), sink


def test_a_repeated_tool_action_gets_retry_count_stamped() -> None:
    logger, sink = _logger()

    logger.action("call", tool="search")
    logger.error("failed", kind="observation", tool="search", error="boom")
    logger.action("call", tool="search")
    logger.action("call", tool="search")

    assert [
        r["meta"].get("retry_count") for r in sink.records if r["meta"]["kind"] == "action"
    ] == [
        None,
        1,
        2,
    ]


def test_a_successful_observation_stops_later_calls_counting_as_retries() -> None:
    logger, sink = _logger()

    for _ in range(3):
        logger.action("call", tool="search")
        logger.observation("done", tool="search")

    assert all("retry_count" not in r["meta"] for r in sink.records)


def test_an_observation_carrying_an_error_does_not_end_the_chain() -> None:
    logger, sink = _logger()

    logger.action("call", tool="search")
    logger.observation("nope", tool="search", error="TimeoutError: slow")
    logger.action("call", tool="search")

    assert sink.records[-1]["meta"]["retry_count"] == 1


def test_an_explicit_retry_count_is_kept() -> None:
    logger, sink = _logger()

    logger.action("call", tool="search")
    logger.action("call", tool="search", retry_count=7)

    assert sink.records[-1]["meta"]["retry_count"] == 7


def test_actions_without_a_tool_are_never_counted() -> None:
    logger, sink = _logger()

    logger.action("think")
    logger.action("think")

    assert all("retry_count" not in r["meta"] for r in sink.records)


def test_the_same_tool_in_different_spans_is_not_a_retry() -> None:
    logger, sink = _logger()

    with logger.span("step-1"):
        logger.action("call", tool="search")
    with logger.span("step-2"):
        logger.action("call", tool="search")

    assert all("retry_count" not in r["meta"] for r in sink.records)


def test_tool_call_id_keeps_two_concurrent_calls_apart() -> None:
    logger, sink = _logger()

    logger.action("call", tool="search", tool_call_id="a")
    logger.action("call", tool="search", tool_call_id="b")
    logger.action("call", tool="search", tool_call_id="a")

    assert [r["meta"].get("retry_count") for r in sink.records] == [None, None, 1]
