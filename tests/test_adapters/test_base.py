from logquill.adapters.base import LogQuillAdapter
from logquill.core.logger import Logger


def test_holds_a_reference_to_the_given_logger() -> None:
    logger = Logger("app.agent")
    adapter = LogQuillAdapter(logger)

    assert adapter.log is logger
