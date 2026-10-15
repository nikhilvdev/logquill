from logquill.core.levels import Level
from logquill.core.logger import Logger


def test_info_produces_well_formed_record() -> None:
    logger = Logger("app.test")
    record = logger.info("hello", user_id=42)

    assert record is not None
    assert record["level"] == "INFO"
    assert record["logger"] == "app.test"
    assert record["message"] == "hello"
    assert record["meta"] == {"user_id": 42}
    assert record["timestamp"].endswith("Z")


def test_level_filtering_drops_below_threshold() -> None:
    logger = Logger("app.test", level=Level.WARN)

    assert logger.debug("noisy") is None
    assert logger.info("still noisy") is None
    assert logger.warn("audible") is not None


def test_set_level_changes_threshold() -> None:
    logger = Logger("app.test", level=Level.ERROR)
    assert logger.warn("dropped") is None

    logger.set_level("trace")
    assert logger.warn("now visible") is not None


def test_all_level_methods_produce_matching_level_name() -> None:
    logger = Logger("app.test", level=Level.TRACE)

    trace = logger.trace("x")
    debug = logger.debug("x")
    info = logger.info("x")
    warn = logger.warn("x")
    error = logger.error("x")
    fatal = logger.fatal("x")

    assert trace is not None and trace["level"] == "TRACE"
    assert debug is not None and debug["level"] == "DEBUG"
    assert info is not None and info["level"] == "INFO"
    assert warn is not None and warn["level"] == "WARN"
    assert error is not None and error["level"] == "ERROR"
    assert fatal is not None and fatal["level"] == "FATAL"


def test_a_meta_key_named_message_does_not_collide_with_the_positional_arg() -> None:
    # `message` is positional-only specifically so this can't raise
    # `TypeError: got multiple values for argument 'message'` — meta is
    # caller-supplied and adversarial input is exactly what it shouldn't
    # be able to crash on.
    logger = Logger("app.test")

    record = logger.info("hello", message="not the real message")

    assert record is not None
    assert record["message"] == "hello"
    assert record["meta"] == {"message": "not the real message"}


def test_a_malformed_exc_info_is_ignored_instead_of_crashing_the_caller() -> None:
    logger = Logger("app.test")

    for bad in ([None, None, []], (1,), "boom", 42, object()):
        record = logger.error("still logs", exc_info=bad)

        assert record is not None
        assert "stack" not in record["meta"]
        assert "exc_info" not in record["meta"]
