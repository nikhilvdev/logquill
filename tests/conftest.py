from __future__ import annotations

from collections.abc import Iterator

import pytest

from logquill.runtime import toggle

try:
    import hypothesis  # noqa: F401
except ImportError:
    # `hypothesis`'s native extension doesn't ship (or build) wheels for
    # free-threaded Python below 3.14 — not a LogQuill limitation, an
    # upstream one. Skip the property-based suites outright rather than
    # failing collection on an environment where the dependency simply
    # can't install.
    collect_ignore_glob = ["test_*_properties.py"]


@pytest.fixture(autouse=True)
def _reset_enable_disable_rules() -> Iterator[None]:
    """`disable()`/`enable()` are process-wide; keep one test's rules from
    silencing the next test's loggers."""
    toggle._reset()
    yield
    toggle._reset()
