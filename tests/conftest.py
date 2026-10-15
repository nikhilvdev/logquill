from __future__ import annotations

from collections.abc import Iterator

import pytest

from logquill.runtime import toggle


@pytest.fixture(autouse=True)
def _reset_enable_disable_rules() -> Iterator[None]:
    """`disable()`/`enable()` are process-wide; keep one test's rules from
    silencing the next test's loggers."""
    toggle._reset()
    yield
    toggle._reset()
