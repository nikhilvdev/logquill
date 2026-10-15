from logquill.core.logger import Logger
from logquill.transports.transport import CollectingTransport


def test_logger_dispatches_to_attached_transports() -> None:
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink])

    record = logger.info("hello", user_id=42)

    assert record is not None
    assert sink.records == [record]
    assert sink.formatted == [sink.formatter.format(record)]


def test_logger_does_not_dispatch_filtered_records() -> None:
    sink = CollectingTransport()
    logger = Logger("app.test", level="warn", transports=[sink])

    assert logger.debug("dropped") is None
    assert sink.records == []


def test_logger_close_closes_every_transport() -> None:
    sink_a, sink_b = CollectingTransport(), CollectingTransport()
    logger = Logger("app.test", transports=[sink_a, sink_b])

    logger.close()

    assert sink_a.closed is True
    assert sink_b.closed is True
