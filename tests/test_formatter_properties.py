from __future__ import annotations

import json
import re
from typing import Any

from adversarial import hostile_text, meta_dicts
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from logquill import JSONFormatter, LogfmtFormatter, TextFormatter, parse_logfmt
from logquill.core.records import LogRecord

_settings = settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow])

_LINE_BREAKS = re.compile(r"[\n\r\x0b\x0c\x1c-\x1e\x85  ]")


def _record(message: str, meta: dict[str, Any]) -> LogRecord:
    return LogRecord(
        timestamp="2026-01-01T00:00:00.000Z",
        level="INFO",
        logger="app.test",
        message=message,
        meta=meta,
    )


@_settings
@given(message=hostile_text, meta=meta_dicts)
def test_text_formatter_never_raises_and_returns_a_string(
    message: str, meta: dict[str, Any]
) -> None:
    line = TextFormatter().format(_record(message, meta))

    assert isinstance(line, str)
    assert line.startswith("2026-01-01T00:00:00.000Z INFO  app.test: ")


@_settings
@given(message=hostile_text, meta=meta_dicts)
def test_logfmt_formatter_never_raises_and_always_emits_exactly_one_line(
    message: str, meta: dict[str, Any]
) -> None:
    line = LogfmtFormatter().format(_record(message, meta))

    assert isinstance(line, str)
    assert not _LINE_BREAKS.search(line)


@_settings
@given(message=hostile_text)
def test_logfmt_round_trips_any_message(message: str) -> None:
    fields = parse_logfmt(LogfmtFormatter().format(_record(message, {})))

    assert fields["message"] == message
    assert fields["level"] == "INFO"
    assert fields["logger"] == "app.test"


_safe_keys = st.from_regex(r"[a-z][a-z0-9_]{0,9}", fullmatch=True).filter(
    lambda key: key not in {"timestamp", "level", "logger", "message"}
)


@_settings
@given(meta=st.dictionaries(_safe_keys, hostile_text, max_size=6))
def test_logfmt_round_trips_string_meta_values(meta: dict[str, str]) -> None:
    fields = parse_logfmt(LogfmtFormatter().format(_record("m", dict(meta))))

    assert {key: fields[key] for key in meta} == meta


@_settings
@given(meta=meta_dicts)
def test_json_formatter_emits_parseable_json_for_any_non_circular_meta(
    meta: dict[str, Any],
) -> None:
    parsed = json.loads(JSONFormatter().format(_record("m", meta)))

    assert parsed["message"] == "m"
    assert isinstance(parsed["meta"], dict)
