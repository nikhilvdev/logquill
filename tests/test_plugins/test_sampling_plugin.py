import pytest

from logquill.core.logger import Logger
from logquill.plugins.sampling_plugin import SamplingPlugin
from logquill.transports.transport import CollectingTransport


def test_rate_zero_drops_everything() -> None:
    logger = Logger("app.test", plugins=[SamplingPlugin(0.0)])

    assert logger.info("hello") is None


def test_rate_one_keeps_everything() -> None:
    logger = Logger("app.test", plugins=[SamplingPlugin(1.0)])

    assert logger.info("hello") is not None


def test_invalid_rate_raises() -> None:
    with pytest.raises(ValueError):
        SamplingPlugin(1.5)


def test_custom_rng_controls_keep_or_drop() -> None:
    keep = SamplingPlugin(0.5, rng=lambda: 0.1)
    drop = SamplingPlugin(0.5, rng=lambda: 0.9)

    logger_keep = Logger("app.test", plugins=[keep])
    logger_drop = Logger("app.test", plugins=[drop])

    assert logger_keep.info("hello") is not None
    assert logger_drop.info("hello") is None


def test_without_transports_a_trace_id_does_not_enable_tail_elevation() -> None:
    # Backward-compatible: no `transports` given means plain rate sampling —
    # level is irrelevant, even for a record that carries a trace id and
    # would otherwise trigger elevation.
    sink = CollectingTransport()
    sampling = SamplingPlugin(0.0, rng=lambda: 0.9)
    logger = Logger("app.test", transports=[sink], plugins=[sampling])

    assert logger.info("step 1", trace_id="t1") is None
    assert logger.error("step 2", trace_id="t1") is None
    assert sink.records == []


def test_tail_based_elevation_flushes_buffered_records_from_the_same_trace() -> None:
    sink = CollectingTransport()
    sampling = SamplingPlugin(0.0, rng=lambda: 0.9, transports=[sink])
    logger = Logger("app.test", transports=[sink], plugins=[sampling])

    assert logger.info("step 1", trace_id="t1") is None
    assert logger.info("step 2", trace_id="t1") is None
    assert sink.records == []

    record = logger.error("step 3", trace_id="t1")

    assert record is not None
    messages = [r["message"] for r in sink.records]
    assert messages == ["step 1", "step 2", "step 3"]


def test_elevation_only_affects_the_matching_trace() -> None:
    sink = CollectingTransport()
    sampling = SamplingPlugin(0.0, rng=lambda: 0.9, transports=[sink])
    logger = Logger("app.test", transports=[sink], plugins=[sampling])

    assert logger.info("other trace", trace_id="t2") is None
    assert logger.info("step 1", trace_id="t1") is None
    logger.error("step 2", trace_id="t1")

    messages = [r["message"] for r in sink.records]
    assert "other trace" not in messages
    assert messages == ["step 1", "step 2"]


def test_records_after_elevation_ship_unconditionally() -> None:
    sink = CollectingTransport()
    sampling = SamplingPlugin(0.0, rng=lambda: 0.9, transports=[sink])
    logger = Logger("app.test", transports=[sink], plugins=[sampling])

    logger.error("triggers elevation", trace_id="t1")
    record = logger.info("after elevation", trace_id="t1")

    assert record is not None
    assert sink.records[-1]["message"] == "after elevation"


def test_buffer_is_bounded_by_max_traces() -> None:
    sink = CollectingTransport()
    sampling = SamplingPlugin(0.0, rng=lambda: 0.9, transports=[sink], max_traces=1)
    logger = Logger("app.test", transports=[sink], plugins=[sampling])

    logger.info("trace one", trace_id="t1")
    logger.info("trace two", trace_id="t2")  # evicts t1's buffer (max_traces=1)
    logger.error("elevates t1", trace_id="t1")

    # t1's earlier buffered record was evicted, so only the elevating record ships
    messages = [r["message"] for r in sink.records]
    assert "trace one" not in messages
    assert "elevates t1" in messages


def test_buffer_is_bounded_by_max_buffered_records() -> None:
    sink = CollectingTransport()
    sampling = SamplingPlugin(0.0, rng=lambda: 0.9, transports=[sink], max_buffered_records=1)
    logger = Logger("app.test", transports=[sink], plugins=[sampling])

    logger.info("trace one, record one", trace_id="t1")
    # second buffered record (still t1) exceeds max_buffered_records=1,
    # evicting the whole oldest trace's buffer (t1's first record)
    logger.info("trace one, record two", trace_id="t1")
    logger.error("elevates t1", trace_id="t1")

    messages = [r["message"] for r in sink.records]
    assert "trace one, record one" not in messages
    assert "elevates t1" in messages
