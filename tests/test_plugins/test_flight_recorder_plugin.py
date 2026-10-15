from __future__ import annotations

from logquill.core.logger import Logger
from logquill.plugins.flight_recorder_plugin import FlightRecorderPlugin
from logquill.transports.transport import CollectingTransport


def test_a_debug_record_below_ship_at_is_buffered_not_shipped() -> None:
    sink = CollectingTransport()
    recorder = FlightRecorderPlugin(transports=[sink])
    logger = Logger("app.test", level="DEBUG", transports=[sink], plugins=[recorder])

    assert logger.debug("context", run_id="r1") is None
    assert sink.records == []


def test_an_info_record_ships_immediately_unbuffered() -> None:
    sink = CollectingTransport()
    recorder = FlightRecorderPlugin(transports=[sink])
    logger = Logger("app.test", level="DEBUG", transports=[sink], plugins=[recorder])

    record = logger.info("normal", run_id="r1")

    assert record is not None
    assert sink.records == [record]


def test_a_record_with_no_run_id_passes_through_regardless_of_level() -> None:
    sink = CollectingTransport()
    recorder = FlightRecorderPlugin(transports=[sink])
    logger = Logger("app.test", level="DEBUG", transports=[sink], plugins=[recorder])

    record = logger.debug("loose")

    assert record is not None
    assert sink.records == [record]


def test_a_failing_run_ships_its_full_debug_trail() -> None:
    sink = CollectingTransport()
    recorder = FlightRecorderPlugin(transports=[sink])
    logger = Logger("app.test", level="DEBUG", transports=[sink], plugins=[recorder])

    logger.debug("step 1", run_id="r1")
    logger.debug("step 2", run_id="r1")
    assert sink.records == []

    record = logger.error("step 3", run_id="r1")

    assert record is not None
    messages = [r["message"] for r in sink.records]
    assert messages == ["step 1", "step 2", "step 3"]


def test_a_passing_run_never_ships_its_debug_trail() -> None:
    sink = CollectingTransport()
    recorder = FlightRecorderPlugin(transports=[sink])
    logger = Logger("app.test", level="DEBUG", transports=[sink], plugins=[recorder])

    logger.debug("step 1", run_id="r1")
    logger.debug("step 2", run_id="r1")
    logger.info("step 3", run_id="r1")  # ships on its own; run never errors

    messages = [r["message"] for r in sink.records]
    assert messages == ["step 3"]


def test_flushing_only_affects_the_matching_run() -> None:
    sink = CollectingTransport()
    recorder = FlightRecorderPlugin(transports=[sink])
    logger = Logger("app.test", level="DEBUG", transports=[sink], plugins=[recorder])

    logger.debug("other run", run_id="r2")
    logger.debug("step 1", run_id="r1")
    logger.error("step 2", run_id="r1")

    messages = [r["message"] for r in sink.records]
    assert "other run" not in messages
    assert messages == ["step 1", "step 2"]


def test_records_after_flushing_ship_unconditionally_even_below_ship_at() -> None:
    sink = CollectingTransport()
    recorder = FlightRecorderPlugin(transports=[sink])
    logger = Logger("app.test", level="DEBUG", transports=[sink], plugins=[recorder])

    logger.error("triggers flush", run_id="r1")
    record = logger.debug("after flush", run_id="r1")

    assert record is not None
    assert sink.records[-1]["message"] == "after flush"


def test_flush_at_can_be_reached_without_meeting_ship_at() -> None:
    # a WARN below the default ship_at (INFO is below WARN, so this is
    # actually above — use a custom, lower ship_at/flush_at pairing instead
    # to exercise flush_at triggering independently of ship_at.
    sink = CollectingTransport()
    recorder = FlightRecorderPlugin(transports=[sink], ship_at="ERROR", flush_at="WARN")
    logger = Logger("app.test", level="DEBUG", transports=[sink], plugins=[recorder])

    logger.info("buffered", run_id="r1")  # below ship_at=ERROR: buffered
    record = logger.warn("flush trigger", run_id="r1")  # below ship_at, but reaches flush_at

    assert record is not None
    messages = [r["message"] for r in sink.records]
    assert messages == ["buffered", "flush trigger"]


def test_buffer_is_bounded_by_max_runs() -> None:
    sink = CollectingTransport()
    recorder = FlightRecorderPlugin(transports=[sink], max_runs=1)
    logger = Logger("app.test", level="DEBUG", transports=[sink], plugins=[recorder])

    logger.debug("run one", run_id="r1")
    logger.debug("run two", run_id="r2")  # evicts r1's buffer (max_runs=1)
    logger.error("errors r1", run_id="r1")

    messages = [r["message"] for r in sink.records]
    assert "run one" not in messages
    assert "errors r1" in messages


def test_buffer_is_bounded_by_max_buffered_records() -> None:
    sink = CollectingTransport()
    recorder = FlightRecorderPlugin(transports=[sink], max_buffered_records=1)
    logger = Logger("app.test", level="DEBUG", transports=[sink], plugins=[recorder])

    logger.debug("run one, record one", run_id="r1")
    logger.debug("run one, record two", run_id="r1")  # evicts r1's first record
    logger.error("errors r1", run_id="r1")

    messages = [r["message"] for r in sink.records]
    assert "run one, record one" not in messages
    assert "errors r1" in messages


def test_a_broken_plugin_pipeline_bounded_memory_under_a_long_passing_run() -> None:
    sink = CollectingTransport()
    recorder = FlightRecorderPlugin(transports=[sink], max_buffered_records=50, max_runs=10)
    logger = Logger("app.test", level="DEBUG", transports=[sink], plugins=[recorder])

    for i in range(5000):
        logger.debug(f"step {i}", run_id=f"r{i % 20}")

    assert recorder._buffered_count <= 50
    assert len(recorder._buffer) <= 10
