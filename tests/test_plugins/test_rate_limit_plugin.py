from __future__ import annotations

import pytest

from logquill.core.logger import Logger
from logquill.plugins.rate_limit_plugin import RateLimitPlugin


def test_allows_up_to_max_records_per_window() -> None:
    logger = Logger("app.test", plugins=[RateLimitPlugin(2, 60.0)])

    assert logger.info("a") is not None
    assert logger.info("b") is not None
    assert logger.info("c") is None


def test_different_levels_have_independent_windows_by_default() -> None:
    logger = Logger("app.test", plugins=[RateLimitPlugin(1, 60.0)])

    assert logger.info("a") is not None
    assert logger.info("b") is None
    assert logger.error("c") is not None


def test_window_resets_after_per_seconds_elapses() -> None:
    now = [0.0]
    logger = Logger("app.test", plugins=[RateLimitPlugin(1, 10.0, clock=lambda: now[0])])

    assert logger.info("a") is not None
    assert logger.info("b") is None

    now[0] = 10.0
    assert logger.info("c") is not None


def test_custom_key_func_groups_by_message() -> None:
    plugin = RateLimitPlugin(1, 60.0, key_func=lambda record: record["message"])
    logger = Logger("app.test", plugins=[plugin])

    assert logger.info("retry") is not None
    assert logger.info("retry") is None
    assert logger.info("other") is not None


def test_max_keys_evicts_oldest_key() -> None:
    plugin = RateLimitPlugin(1, 60.0, key_func=lambda record: record["message"], max_keys=2)
    logger = Logger("app.test", plugins=[plugin])

    assert logger.info("k1") is not None
    assert logger.info("k2") is not None
    assert logger.info("k3") is not None  # evicts k1's window

    # k1 was evicted, so it's treated as a fresh key and allowed again.
    assert logger.info("k1") is not None


def test_invalid_max_records_raises() -> None:
    with pytest.raises(ValueError):
        RateLimitPlugin(0, 60.0)


def test_invalid_per_seconds_raises() -> None:
    with pytest.raises(ValueError):
        RateLimitPlugin(1, 0.0)
