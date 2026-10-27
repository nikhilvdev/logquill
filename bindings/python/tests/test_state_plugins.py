"""Sampling, rate limiting, adaptive sampling, the flight recorder and the run summary, whose state
machines now run in the Rust core."""

import pytest

from logquill import (
    AdaptiveSamplingPlugin,
    CollectingTransport,
    FlightRecorderPlugin,
    Logger,
    RateLimitPlugin,
    RunPlugin,
    RunSummaryPlugin,
    SamplingPlugin,
)


def rec(level="INFO", message="m", logger="app", **meta):
    return {
        "schema_version": "2.0",
        "timestamp": "2026-01-01T00:00:00.000Z",
        "level": level,
        "logger": logger,
        "message": message,
        "meta": meta,
    }


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


# ---- SamplingPlugin ------------------------------------------------------------------------


def test_sampling_keeps_records_whose_draw_is_below_the_rate():
    plugin = SamplingPlugin(0.5, rng=iter([0.1, 0.9, 0.49, 0.5]).__next__)
    assert [plugin.before_log(rec()) is not None for _ in range(4)] == [True, False, True, False]


def test_sampling_at_the_extremes_keeps_everything_or_nothing():
    assert all(SamplingPlugin(1.0).before_log(rec()) for _ in range(50))
    assert not any(SamplingPlugin(0.0).before_log(rec()) for _ in range(50))


@pytest.mark.parametrize("rate", [-0.1, 1.5])
def test_sampling_refuses_a_rate_outside_zero_to_one(rate):
    with pytest.raises(ValueError, match="rate must be between 0 and 1"):
        SamplingPlugin(rate)


def test_sampling_a_trace_that_errors_is_released_whole():
    sink = CollectingTransport()
    plugin = SamplingPlugin(0.0, transports=[sink])
    log = Logger("app", transports=[sink], plugins=[plugin], async_dispatch=False)
    log.info("start", trace_id="t1")
    log.info("middle", trace_id="t1")
    log.info("other trace", trace_id="t2")
    assert sink.records == [] and plugin.buffered_records == 3
    log.error("boom", trace_id="t1")
    assert [r["message"] for r in sink.records] == ["start", "middle", "boom"]
    log.info("after", trace_id="t1")
    assert sink.records[-1]["message"] == "after", "an elevated trace ships from then on"
    assert plugin.buffered_records == 1, "only the unrelated trace is still buffered"
    log.info("t2 is still sampled out", trace_id="t2")
    assert "t2 is still sampled out" not in [r["message"] for r in sink.records]


def test_sampling_without_transports_never_buffers_or_elevates():
    plugin = SamplingPlugin(0.0)
    assert plugin.before_log(rec(trace_id="t1")) is None
    assert plugin.before_log(rec("ERROR", trace_id="t1")) is None
    assert plugin.buffered_records == 0


def test_sampling_a_record_with_no_trace_id_is_just_rate_sampled():
    sink = CollectingTransport()
    plugin = SamplingPlugin(0.0, transports=[sink])
    assert plugin.before_log(rec()) is None
    assert plugin.buffered_records == 0


def test_sampling_buffers_are_bounded_and_evict_the_oldest_trace():
    sink = CollectingTransport()
    plugin = SamplingPlugin(0.0, transports=[sink], max_traces=2, max_buffered_records=100)
    for trace in ("a", "b", "c"):
        plugin.before_log(rec(trace_id=trace))
    assert plugin.buffered_records == 2
    plugin.before_log(rec("ERROR", trace_id="a"))
    assert sink.records == [], "trace a was evicted, so nothing of it is released"
    capped = SamplingPlugin(0.0, transports=[sink], max_buffered_records=5)
    for i in range(20):
        capped.before_log(rec(trace_id="same", i=i))
    assert capped.buffered_records <= 5


def test_sampling_elevate_at_and_trace_key_are_configurable():
    sink = CollectingTransport()
    plugin = SamplingPlugin(0.0, transports=[sink], trace_key="req", elevate_at="WARN")
    plugin.before_log(rec(req="r1", n=1))
    plugin.before_log(rec("WARN", req="r1", n=2))
    assert [r["meta"]["n"] for r in sink.records] == [1]


# ---- RateLimitPlugin -----------------------------------------------------------------------


def test_rate_limit_allows_the_budget_then_drops_until_the_window_rolls():
    clock = Clock()
    plugin = RateLimitPlugin(3, 10, clock=clock)
    assert [plugin.before_log(rec()) is not None for _ in range(5)] == [True] * 3 + [False] * 2
    clock.now += 9.9
    assert plugin.before_log(rec()) is None
    clock.now += 0.2
    assert plugin.before_log(rec()) is not None, "a new window"


def test_rate_limit_counts_each_logger_and_level_separately():
    plugin = RateLimitPlugin(1, 60, clock=Clock())
    assert plugin.before_log(rec("INFO", logger="a")) is not None
    assert plugin.before_log(rec("INFO", logger="a")) is None
    assert plugin.before_log(rec("INFO", logger="b")) is not None
    assert plugin.before_log(rec("ERROR", logger="a")) is not None


def test_rate_limit_forgets_the_least_recent_key_beyond_max_keys():
    clock = Clock()
    plugin = RateLimitPlugin(1, 60, max_keys=2, clock=clock)
    plugin.before_log(rec(logger="a"))
    plugin.before_log(rec(logger="b"))
    plugin.before_log(rec(logger="c"))
    assert plugin.before_log(rec(logger="a")) is not None, "a's window was evicted"
    assert plugin.before_log(rec(logger="c")) is None


def test_rate_limit_with_a_custom_key_function_uses_the_python_path():
    plugin = RateLimitPlugin(1, 60, key_func=lambda r: r["message"], clock=Clock())
    assert plugin._native is None
    assert plugin.before_log(rec(message="x")) is not None
    assert plugin.before_log(rec(message="x")) is None
    assert plugin.before_log(rec(message="y")) is not None


def test_rate_limit_refuses_bad_settings():
    with pytest.raises(ValueError, match="max_records"):
        RateLimitPlugin(0, 1)
    with pytest.raises(ValueError, match="per_seconds"):
        RateLimitPlugin(1, 0)


# ---- AdaptiveSamplingPlugin ----------------------------------------------------------------


def test_adaptive_always_keeps_errors_and_slow_spans():
    plugin = AdaptiveSamplingPlugin(base_rate=0.0, min_rate=0.0, rng=lambda: 0.99, clock=Clock())
    assert plugin.before_log(rec("ERROR")) is not None
    assert plugin.before_log(rec(kind="span", duration_ms=1500.0)) is not None
    assert plugin.before_log(rec(kind="span", duration_ms=10.0)) is None
    assert plugin.before_log(rec()) is None


def test_adaptive_rate_rises_when_traffic_is_light_and_falls_when_it_is_heavy():
    clock = Clock()
    plugin = AdaptiveSamplingPlugin(
        base_rate=0.5, max_bytes_per_second=1000, rng=lambda: 0.0, clock=clock
    )
    clock.now += 1.0
    plugin.before_log(rec())  # an idle window rolled: demand well under budget
    assert plugin.rate == pytest.approx(0.6)
    for _ in range(40):
        plugin.before_log(rec(note="x" * 50))  # far more than 1000 bytes asked for
    clock.now += 1.0
    plugin.before_log(rec())
    assert plugin.rate == pytest.approx(0.3)


def test_adaptive_caps_the_bytes_that_pass_in_a_window():
    plugin = AdaptiveSamplingPlugin(
        base_rate=1.0, max_bytes_per_second=500, rng=lambda: 0.0, clock=Clock()
    )
    passed = sum(plugin.before_log(rec(note="x" * 60)) is not None for _ in range(50))
    assert 0 < passed < 50


def test_adaptive_refuses_bad_rates():
    with pytest.raises(ValueError):
        AdaptiveSamplingPlugin(base_rate=1.5)
    with pytest.raises(ValueError, match="min_rate"):
        AdaptiveSamplingPlugin(min_rate=0.9, max_rate=0.1)


# ---- FlightRecorderPlugin ------------------------------------------------------------------


def test_a_failing_run_ships_its_debug_trail_and_a_passing_run_ships_none():
    sink = CollectingTransport()
    log = Logger(
        "app",
        level="DEBUG",
        transports=[sink],
        plugins=[FlightRecorderPlugin(transports=[sink])],
        async_dispatch=False,
    )
    for run in ("good", "bad"):
        log.debug("step 1", run_id=run)
        log.debug("step 2", run_id=run)
    log.info("kept anyway", run_id="good")
    log.error("failed", run_id="bad")
    log.debug("after the failure", run_id="bad")
    messages = [(r["meta"]["run_id"], r["message"]) for r in sink.records]
    assert ("good", "step 1") not in messages and ("good", "kept anyway") in messages
    assert [m for run, m in messages if run == "bad"] == [
        "step 1",
        "step 2",
        "failed",
        "after the failure",
    ]


def test_a_record_with_no_run_id_passes_through_at_any_level():
    sink = CollectingTransport()
    plugin = FlightRecorderPlugin(transports=[sink])
    assert plugin.before_log(rec("DEBUG")) is not None


def test_the_flight_recorder_buffer_is_bounded():
    sink = CollectingTransport()
    plugin = FlightRecorderPlugin(transports=[sink], max_buffered_records=10, max_runs=3)
    for run in range(5):
        for _ in range(8):
            plugin.before_log(rec("DEBUG", run_id=f"r{run}"))
    assert plugin.buffered_records <= 10
    plugin.before_log(rec("ERROR", run_id="r0"))
    assert sink.records == [], "the oldest run was evicted before it failed"


# ---- RunSummaryPlugin ----------------------------------------------------------------------


def test_one_summary_is_written_when_the_outermost_span_closes():
    sink = CollectingTransport()
    log = Logger(
        "agent",
        transports=[sink],
        plugins=[RunPlugin(run_id="run1"), RunSummaryPlugin(transports=[sink])],
        async_dispatch=False,
    )
    with log.span("run"):
        log.llm_call(model="m", tokens_in=100, tokens_out=40, cost_usd=0.5)
        log.llm_call(model="m", tokens_in=10, tokens_out=5, cost_usd=0.25)
        log.action("search", tool="web", tool_call_id="1")
        log.action("search again", tool="web", tool_call_id="1")
        log.error("oops")
    summaries = [r for r in sink.records if r["meta"].get("kind") == "run_summary"]
    assert len(summaries) == 1
    meta = summaries[0]["meta"]
    assert (meta["tokens_in"], meta["tokens_out"], meta["cost_usd"]) == (110, 45, 0.75)
    assert (meta["tool_calls"], meta["retries"], meta["errors"]) == (2, 1, 1)
    assert meta["run_id"] == "run1" and meta["record_count"] == 6 and "duration_ms" in meta
    assert summaries[0]["logger"] == "app.run_summary" and summaries[0]["message"] == "run summary"
    with log.span("second run"):
        pass
    assert len([r for r in sink.records if r["meta"].get("kind") == "run_summary"]) == 2


def test_a_nested_span_does_not_close_the_run():
    sink = CollectingTransport()
    log = Logger(
        "agent",
        transports=[sink],
        plugins=[RunPlugin(run_id="r"), RunSummaryPlugin(transports=[sink], logger_name="sum")],
        async_dispatch=False,
    )
    with log.span("outer"):
        with log.span("inner"):
            pass
        assert not [r for r in sink.records if r["logger"] == "sum"]
    assert len([r for r in sink.records if r["logger"] == "sum"]) == 1


def test_run_summary_bounds_the_runs_it_tracks():
    sink = CollectingTransport()
    plugin = RunSummaryPlugin(transports=[sink], max_runs=2)
    for run in ("a", "b", "c"):
        plugin.after_log(rec(run_id=run))
    root = rec(kind="span", span_id="s", run_id="a")
    plugin.after_log(root)
    summaries = [r for r in sink.records if r["meta"].get("kind") == "run_summary"]
    assert summaries[0]["meta"]["record_count"] == 1, "a's earlier records were forgotten"
