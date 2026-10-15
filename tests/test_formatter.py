import json

from logquill.core.logger import Logger
from logquill.formatter import JSONFormatter


def test_json_formatter_round_trips_record() -> None:
    logger = Logger("app.test")
    record = logger.info("hello", user_id=42)
    assert record is not None

    formatted = JSONFormatter().format(record)
    parsed = json.loads(formatted)

    assert parsed == record
