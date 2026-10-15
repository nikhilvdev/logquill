from __future__ import annotations

from logquill.core.logger import Logger
from logquill.core.records import LogRecord
from logquill.plugins.plugin import Plugin
from logquill.transports.transport import CollectingTransport


class UppercasePlugin(Plugin):
    def before_log(self, record: LogRecord) -> LogRecord | None:
        record["message"] = record["message"].upper()
        return record


class DroppingPlugin(Plugin):
    def before_log(self, record: LogRecord) -> LogRecord | None:
        return None


class SpyPlugin(Plugin):
    def __init__(self) -> None:
        self.errors: list[tuple[Exception, LogRecord]] = []
        self.after_log_calls: list[LogRecord] = []

    def after_log(self, record: LogRecord) -> None:
        self.after_log_calls.append(record)

    def on_error(self, exc: Exception, record: LogRecord) -> None:
        self.errors.append((exc, record))


class BrokenBeforeLogPlugin(SpyPlugin):
    def before_log(self, record: LogRecord) -> LogRecord | None:
        raise RuntimeError("boom")


class BrokenAfterLogPlugin(SpyPlugin):
    def after_log(self, record: LogRecord) -> None:
        super().after_log(record)
        raise RuntimeError("boom")


def test_before_log_can_transform_the_record() -> None:
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], plugins=[UppercasePlugin()])

    record = logger.info("hello")

    assert record is not None
    assert record["message"] == "HELLO"
    assert sink.records == [record]


def test_before_log_returning_none_drops_the_record() -> None:
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], plugins=[DroppingPlugin()])

    assert logger.info("hello") is None
    assert sink.records == []


def test_broken_before_log_does_not_crash_logging() -> None:
    sink = CollectingTransport()
    broken = BrokenBeforeLogPlugin()
    logger = Logger("app.test", transports=[sink], plugins=[broken])

    record = logger.info("hello")

    assert record is not None
    assert sink.records == [record]
    assert len(broken.errors) == 1
    assert isinstance(broken.errors[0][0], RuntimeError)


def test_broken_before_log_does_not_stop_remaining_plugins_from_running() -> None:
    sink = CollectingTransport()
    plugins = [BrokenBeforeLogPlugin(), UppercasePlugin()]
    logger = Logger("app.test", transports=[sink], plugins=plugins)

    record = logger.info("hello")

    assert record is not None
    assert record["message"] == "HELLO"


def test_broken_after_log_does_not_crash_logging_or_skip_remaining_plugins() -> None:
    broken = BrokenAfterLogPlugin()
    spy = SpyPlugin()
    logger = Logger("app.test", plugins=[broken, spy])

    record = logger.info("hello")

    assert record is not None
    assert broken.after_log_calls == [record]
    assert len(broken.errors) == 1
    assert spy.after_log_calls == [record]


def test_use_registers_a_plugin_and_returns_self_for_chaining() -> None:
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink])

    result = logger.use(UppercasePlugin())

    assert result is logger
    logger.info("hi")
    assert sink.records[0]["message"] == "HI"


def test_use_accepts_a_plain_function_as_middleware() -> None:
    def strip_ssn(record: LogRecord) -> LogRecord | None:
        record["meta"].pop("ssn", None)
        return record

    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink])

    logger.use(strip_ssn)
    record = logger.info("submit", ssn="123-45-6789", user_id=42)

    assert record is not None
    assert "ssn" not in record["meta"]
    assert record["meta"]["user_id"] == 42


def test_function_middleware_behaves_identically_to_an_equivalent_plugin() -> None:
    def uppercase(record: LogRecord) -> LogRecord | None:
        record["message"] = record["message"].upper()
        return record

    function_sink = CollectingTransport()
    function_logger = Logger("app.test", transports=[function_sink])
    function_logger.use(uppercase)

    plugin_sink = CollectingTransport()
    plugin_logger = Logger("app.test", transports=[plugin_sink], plugins=[UppercasePlugin()])

    function_logger.info("hello")
    plugin_logger.info("hello")

    assert function_sink.records[0]["message"] == plugin_sink.records[0]["message"] == "HELLO"


def test_use_accepting_a_function_returning_none_drops_the_record() -> None:
    def drop_everything(record: LogRecord) -> LogRecord | None:
        return None

    logger = Logger("app.test")
    logger.use(drop_everything)

    assert logger.info("hello") is None


def test_constructor_plugins_list_accepts_functions_too() -> None:
    def strip_ssn(record: LogRecord) -> LogRecord | None:
        record["meta"].pop("ssn", None)
        return record

    logger = Logger("app.test", plugins=[strip_ssn])

    record = logger.info("submit", ssn="123-45-6789")

    assert record is not None
    assert "ssn" not in record["meta"]
