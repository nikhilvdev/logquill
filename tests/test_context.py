from __future__ import annotations

import asyncio
import concurrent.futures

from logquill.core.context import bind_context, current_context
from logquill.core.logger import Logger


def test_bound_context_appears_in_meta_without_manual_passing() -> None:
    logger = Logger("app.test")

    def nested_call() -> object:
        return logger.info("handled")

    with bind_context(request_id="abc123"):
        record = nested_call()

    assert record is not None
    assert record["meta"]["request_id"] == "abc123"


def test_context_not_visible_outside_the_block() -> None:
    logger = Logger("app.test")

    record = logger.info("before")
    assert record is not None
    assert "request_id" not in record["meta"]

    with bind_context(request_id="abc123"):
        pass

    record = logger.info("after")
    assert record is not None
    assert "request_id" not in record["meta"]


def test_call_site_meta_overrides_bound_context() -> None:
    logger = Logger("app.test")

    with bind_context(env="prod"):
        record = logger.info("hello", env="staging")

    assert record is not None
    assert record["meta"]["env"] == "staging"


def test_nested_bind_context_merges_with_inner_winning() -> None:
    logger = Logger("app.test")

    with bind_context(a=1, b=1):
        with bind_context(b=2, c=2):
            record = logger.info("hello")
        after_inner = current_context()

    assert record is not None
    assert record["meta"] == {"a": 1, "b": 2, "c": 2}
    assert after_inner == {"a": 1, "b": 1}


def test_context_is_isolated_per_thread() -> None:
    logger = Logger("app.test")
    results: dict[str, object] = {}

    def worker(name: str) -> None:
        with bind_context(worker=name):
            results[name] = logger.info("hello")

    with concurrent.futures.ThreadPoolExecutor() as pool:
        list(pool.map(worker, ["t1", "t2", "t3"]))

    for name in ["t1", "t2", "t3"]:
        record = results[name]
        assert record is not None
        assert record["meta"]["worker"] == name


def test_context_is_isolated_per_asyncio_task() -> None:
    logger = Logger("app.test")

    async def worker(name: str) -> object:
        with bind_context(task=name):
            await asyncio.sleep(0)
            return logger.info("hello")

    async def run() -> list[object]:
        return await asyncio.gather(worker("a"), worker("b"))

    results = asyncio.run(run())
    assert [r["meta"]["task"] for r in results] == ["a", "b"]
