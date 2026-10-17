from __future__ import annotations

from typing import Callable

import pytest

from logquill import Level, Logger
from logquill.transports.transport import CollectingTransport


def _logger(level: str = "DEBUG") -> tuple[Logger, CollectingTransport]:
    sink = CollectingTransport()
    return Logger("app.test", level=level, transports=[sink]), sink


def test_lazy_values_are_not_evaluated_when_the_level_filters_the_call_out() -> None:
    logger, sink = _logger(level="INFO")
    calls: list[int] = []

    result = logger.opt(lazy=True).debug("state", dump=lambda: calls.append(1))

    assert result is None
    assert calls == []
    assert sink.records == []


def test_lazy_values_are_evaluated_when_the_record_is_emitted() -> None:
    logger, sink = _logger()

    logger.opt(lazy=True).debug("state", total=lambda: 2 + 2, plain=5)

    assert sink.records[0]["meta"] == {"total": 4, "plain": 5}


def test_lazy_values_are_not_evaluated_for_a_disabled_logger() -> None:
    import logquill

    logger, _ = _logger()
    logquill.disable("app")
    calls: list[int] = []

    logger.opt(lazy=True).info("x", v=lambda: calls.append(1))

    assert calls == []


def test_a_raising_lazy_value_becomes_a_placeholder_instead_of_crashing_the_caller() -> None:
    logger, sink = _logger()

    logger.opt(lazy=True).info("x", bad=lambda: 1 / 0, good=lambda: "ok")

    meta = sink.records[0]["meta"]
    assert meta["bad"] == "<lazy value raised ZeroDivisionError: division by zero>"
    assert meta["good"] == "ok"


def test_callables_are_left_alone_without_lazy() -> None:
    logger, sink = _logger()

    def fn() -> None: ...

    logger.opt().info("x", callback=fn)

    assert sink.records[0]["meta"]["callback"] is fn


def test_async_dispatch_evaluates_lazy_values_on_the_calling_thread() -> None:
    import threading

    sink = CollectingTransport()
    logger = Logger("app", level="DEBUG", transports=[sink], async_dispatch=True)
    seen: list[int] = []

    logger.opt(lazy=True).info("x", v=lambda: seen.append(threading.get_ident()))
    logger.close()

    assert seen == [threading.get_ident()]


def test_depth_zero_reports_the_direct_caller() -> None:
    logger, sink = _logger()

    logger.opt(depth=0).info("here")

    caller = sink.records[0]["meta"]["caller"]
    assert caller["function"] == "test_depth_zero_reports_the_direct_caller"
    assert caller["module"] == __name__
    assert caller["file"].endswith("test_opt.py")
    assert isinstance(caller["line"], int)


def test_depth_one_skips_a_wrapper_and_reports_its_caller() -> None:
    logger, sink = _logger()

    def wrapper(message: str) -> None:
        logger.opt(depth=1).info(message)

    def business_logic() -> None:
        wrapper("via wrapper")

    business_logic()

    assert sink.records[0]["meta"]["caller"]["function"] == "business_logic"


def test_depth_works_for_every_method_family() -> None:
    logger, sink = _logger(level="TRACE")
    view = logger.opt(depth=0)

    for method in (
        view.trace,
        view.debug,
        view.info,
        view.warn,
        view.error,
        view.fatal,
        view.thought,
        view.action,
        view.observation,
        view.decision,
    ):
        method("m")

    functions = {record["meta"]["caller"]["function"] for record in sink.records}
    assert functions == {"test_depth_works_for_every_method_family"}
    assert [r["level"] for r in sink.records][:6] == [level.name for level in Level]
    assert sink.records[6]["meta"]["kind"] == "thought"


def test_depth_beyond_the_stack_omits_caller_instead_of_raising() -> None:
    logger, sink = _logger()

    logger.opt(depth=10_000).info("x")

    assert "caller" not in sink.records[0]["meta"]


def test_an_explicit_caller_in_meta_wins() -> None:
    logger, sink = _logger()

    logger.opt(depth=0).info("x", caller="mine")

    assert sink.records[0]["meta"]["caller"] == "mine"


def test_no_caller_is_recorded_without_depth() -> None:
    logger, sink = _logger()

    logger.info("x")
    logger.opt(lazy=True).info("y")

    assert all("caller" not in record["meta"] for record in sink.records)


def test_negative_depth_is_rejected() -> None:
    logger, _ = _logger()

    with pytest.raises(ValueError, match="depth"):
        logger.opt(depth=-1)


def test_opt_with_the_same_options_returns_the_same_cached_instance() -> None:
    """The common idiom is `logger.opt(lazy=True).debug(...)` called inline
    on every log call — this proves it doesn't allocate a fresh `OptLogger`
    each time."""
    logger, _ = _logger()

    first = logger.opt(lazy=True, depth=1)
    second = logger.opt(lazy=True, depth=1)

    assert first is second


def test_opt_with_different_options_returns_different_instances() -> None:
    logger, _ = _logger()

    assert logger.opt(lazy=True) is not logger.opt(lazy=False)
    assert logger.opt(depth=0) is not logger.opt(depth=1)
    assert logger.opt(depth=None) is not logger.opt(depth=0)


def test_a_cached_opt_view_still_behaves_correctly_across_repeated_use() -> None:
    logger, sink = _logger(level="INFO")
    calls: list[int] = []

    def expensive(value: int) -> Callable[[], int]:
        def compute() -> int:
            calls.append(value)
            return value

        return compute

    view = logger.opt(lazy=True)
    assert view.debug("filtered out", dump=expensive(1)) is None
    assert calls == []

    record = view.info("emitted", dump=expensive(2))
    assert record is not None
    assert calls == [2]
    assert sink.records[0]["meta"]["dump"] == 2


def test_the_opt_cache_is_bounded() -> None:
    logger, _ = _logger()

    for depth in range(64):
        logger.opt(depth=depth)

    assert len(logger._opt_cache) <= logger._OPT_CACHE_MAX
