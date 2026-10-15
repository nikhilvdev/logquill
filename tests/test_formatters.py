from __future__ import annotations

import io

from logquill import (
    ConsoleTransport,
    LogfmtFormatter,
    Logger,
    TextFormatter,
    parse_logfmt,
)
from logquill.core.levels import Level
from logquill.core.records import LogRecord, create_record


def _record(message: str = "user signed up", **meta: object) -> LogRecord:
    record = create_record(level=Level.INFO, logger="app.api", message=message, meta=dict(meta))
    record["timestamp"] = "2026-01-01T00:00:00.000Z"
    return record


def test_old_import_path_still_works() -> None:
    from logquill.formatter import Formatter, JSONFormatter
    from logquill.formatters import Formatter as NewFormatter
    from logquill.formatters import JSONFormatter as NewJSONFormatter

    assert Formatter is NewFormatter
    assert JSONFormatter is NewJSONFormatter


# --- text -----------------------------------------------------------------


def test_text_formatter_renders_one_readable_line() -> None:
    line = TextFormatter().format(_record(user_id=42))

    assert line == '2026-01-01T00:00:00.000Z INFO  app.api: user signed up {"user_id":42}'


def test_text_formatter_omits_meta_when_empty() -> None:
    assert (
        TextFormatter().format(_record())
        == "2026-01-01T00:00:00.000Z INFO  app.api: user signed up"
    )


def test_text_formatter_prints_a_stack_on_following_lines() -> None:
    stack = 'Traceback (most recent call last):\n  File "x.py", line 1\nValueError: nope\n'

    line = TextFormatter().format(_record("failed", stack=stack, attempt=2))

    first, *rest = line.split("\n")
    assert first.endswith('failed {"attempt":2}')
    assert rest == stack.rstrip("\n").split("\n")


def test_text_formatter_survives_circular_and_unprintable_meta() -> None:
    class Unprintable:
        def __repr__(self) -> str:
            raise RuntimeError("no repr")

        __str__ = __repr__

    loop: dict[str, object] = {}
    loop["self"] = loop

    line = TextFormatter().format(_record(loop=loop, bad=Unprintable()))

    assert line.startswith("2026-01-01T00:00:00.000Z INFO  app.api: user signed up ")
    assert "<unrepresentable Unprintable>" in line


def test_console_transport_with_text_formatter() -> None:
    out = io.StringIO()
    logger = Logger(
        "app",
        transports=[ConsoleTransport(formatter=TextFormatter(), colorize=False, stdout=out)],
    )

    logger.info("hello", n=1)

    assert out.getvalue().endswith('app: hello {"n":1}\n')


# --- logfmt ---------------------------------------------------------------


def test_logfmt_formatter_emits_core_fields_then_meta() -> None:
    line = LogfmtFormatter().format(_record(user_id=42, plan="pro"))

    assert line == (
        "timestamp=2026-01-01T00:00:00.000Z level=INFO logger=app.api "
        'message="user signed up" user_id=42 plan=pro'
    )


def test_logfmt_quotes_and_escapes_values_so_the_line_stays_single_line() -> None:
    line = LogfmtFormatter().format(_record(note='a "quoted"\nline\twith = sign', empty=""))

    assert "\n" not in line and "\t" not in line
    assert r'note="a \"quoted\"\nline\twith = sign"' in line
    assert 'empty=""' in line


def test_logfmt_flattens_nested_dicts_and_encodes_scalars() -> None:
    line = LogfmtFormatter().format(
        _record(http={"status": 200, "ok": True, "body": None}, tags=["a", "b"])
    )

    assert "http.status=200 http.ok=true http.body=null" in line
    assert 'tags="[\\"a\\",\\"b\\"]"' in line


def test_logfmt_prefixes_meta_keys_that_shadow_core_fields() -> None:
    record = _record()
    record["meta"] = {"level": "custom", "message": "also"}

    line = LogfmtFormatter().format(record)

    fields = parse_logfmt(line)
    assert fields["level"] == "INFO"
    assert fields["message"] == "user signed up"
    assert fields["meta.level"] == "custom"
    assert fields["meta.message"] == "also"


def test_logfmt_sanitizes_awkward_keys() -> None:
    line = LogfmtFormatter().format(_record(**{"has space": 1, "a=b": 2, "": 3}))

    assert "has_space=1" in line and "a_b=2" in line and " _=3" in line


def test_logfmt_deep_nesting_falls_back_to_a_json_value() -> None:
    deep: dict[str, object] = {}
    node = deep
    for _ in range(20):
        child: dict[str, object] = {}
        node["n"] = child
        node = child
    node["leaf"] = 1

    line = LogfmtFormatter().format(_record(deep=deep))

    assert "\n" not in line
    assert line.count("=") < 15  # bounded, not one pair per level


def test_logfmt_round_trips_through_parse_logfmt() -> None:
    record = _record(note='tricky "value" \\ with\nnewline', n=7, empty="")

    fields = parse_logfmt(LogfmtFormatter().format(record))

    assert fields["note"] == 'tricky "value" \\ with\nnewline'
    assert fields["n"] == "7"
    assert fields["empty"] == ""
    assert fields["message"] == "user signed up"


def test_text_and_logfmt_show_an_llm_block() -> None:
    record = _record("chat", kind="action")
    record["llm"] = {"model": "m1", "tokens_in": 5, "cost_usd": 0.25}

    assert 'llm={"model":"m1","tokens_in":5,"cost_usd":0.25}' in TextFormatter().format(record)
    fields = parse_logfmt(LogfmtFormatter().format(record))
    assert fields["llm.model"] == "m1"
    assert fields["llm.tokens_in"] == "5"
    assert fields["kind"] == "action"


def test_the_text_pattern_reads_an_llm_block_back() -> None:
    from logquill import TEXT_LOG_CASTS, TEXT_LOG_PATTERN, parse

    record = _record("chat", kind="action")
    record["llm"] = {"model": "m1", "tokens_in": 5}

    (entry,) = parse([TextFormatter().format(record)], TEXT_LOG_PATTERN, cast=TEXT_LOG_CASTS)

    assert entry["message"] == "chat"
    assert entry["llm"] == {"model": "m1", "tokens_in": 5}
    assert entry["meta"] == {"kind": "action"}
