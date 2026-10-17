import json
import sys

import pytest

from logquill.core.logger import Logger
from logquill.formatter import JSONFormatter


def test_json_formatter_round_trips_record() -> None:
    logger = Logger("app.test")
    record = logger.info("hello", user_id=42)
    assert record is not None

    formatted = JSONFormatter().format(record)
    parsed = json.loads(formatted)

    assert parsed == record


def test_an_unknown_backend_raises_right_away() -> None:
    with pytest.raises(ValueError, match="backend must be 'json' or 'orjson'"):
        JSONFormatter(backend="yaml")  # type: ignore[arg-type]


def test_orjson_backend_without_orjson_installed_raises_an_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "orjson", None)

    with pytest.raises(ImportError, match=r"pip install logquill\[fast\]"):
        JSONFormatter(backend="orjson")


def test_orjson_backend_produces_the_same_json_as_the_stdlib_backend() -> None:
    pytest.importorskip("orjson")
    logger = Logger("app.test")
    record = logger.info("hello", user_id=42, tag=object())
    assert record is not None

    stdlib_formatted = JSONFormatter(backend="json").format(record)
    orjson_formatted = JSONFormatter(backend="orjson").format(record)

    assert json.loads(orjson_formatted) == json.loads(stdlib_formatted)


def test_orjson_backend_raises_right_away_not_on_first_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "orjson", None)

    with pytest.raises(ImportError):
        JSONFormatter(backend="orjson")
