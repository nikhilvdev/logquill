import re
from pathlib import Path

import logquill


def test_version_is_a_string() -> None:
    assert isinstance(logquill.__version__, str)


def test_version_matches_pyproject() -> None:
    # `tomllib` only exists from Python 3.11, and the floor is 3.10, so read
    # the one line we need instead of adding a TOML dependency.
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), re.MULTILINE)

    assert match is not None, 'pyproject.toml has no top-level `version = "..."` line'
    assert logquill.__version__ == match.group(1), (
        f"logquill.__version__ is {logquill.__version__!r} but pyproject.toml says "
        f"{match.group(1)!r} — bump both together"
    )
