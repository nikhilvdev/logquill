from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any

from logquill import _native

#: Matches one entry written by `TextFormatter` (single-line entries; a
#: traceback printed on the lines after an entry isn't part of the match).
#: Pass with `cast=TEXT_LOG_CASTS` to get `meta` (and an LLM call's `llm`) back as dicts:
#:
#:     parse("app.log", TEXT_LOG_PATTERN, cast=TEXT_LOG_CASTS)
TEXT_LOG_PATTERN = (
    r"^(?P<timestamp>\S+) (?P<level>[A-Z]+)\s+(?P<logger>[^:\s]+): "
    r"(?P<message>.*?)(?: llm=(?P<llm>\{[^{}]*\}))?(?: (?P<meta>\{.*\}))?$"
)

#: `cast` mapping that decodes the `meta` and `llm` groups of `TEXT_LOG_PATTERN` from JSON.
TEXT_LOG_CASTS: dict[str, Callable[[str], Any]] = {"meta": json.loads, "llm": json.loads}


def parse(
    source: str | os.PathLike[str] | Iterable[str],
    pattern: str | re.Pattern[str],
    *,
    cast: Mapping[str, Callable[[str], Any]] | None = None,
    encoding: str = "utf-8",
) -> Iterator[dict[str, Any]]:
    r"""Extract structured fields from a log file with a regex, yielding one
    dict per matching line — including logs LogQuill didn't write (legacy
    apps, third-party tools), which is the reason to reach for this over
    `json.loads`:

        pattern = r"(?P<when>\S+ \S+) \[(?P<level>[A-Z]+)\] (?P<code>\d+) (?P<message>.*)"
        for entry in parse("legacy.log", pattern, cast={"code": int}):
            if entry["level"] == "ERROR" and entry["code"] >= 500:
                print(entry["when"], entry["message"])

    `source` is a path (`str` or `os.PathLike`), or any iterable of lines
    such as an open file or `sys.stdin`. `pattern` must contain at least one
    named group, `(?P<name>...)`; each yielded dict maps group names to the
    matched text (`None` for an optional group that didn't participate).
    Lines that don't match are skipped. `cast` maps group names to a
    function applied to that group's text (`{"status": int}`); a group that
    is `None` is left as is.

    Streams line by line, so memory stays flat however large the file is.
    A pattern is matched against one line at a time, so it can't span
    multiple lines (e.g. a wrapped traceback).

    Raises `ValueError` immediately for a pattern with no named groups or a
    `cast` key that isn't one, and — while iterating — if a `cast` function
    rejects a value, naming the line number so the entry can be found.
    """
    compiled = re.compile(pattern) if isinstance(pattern, str) else pattern
    if not compiled.groupindex:
        raise ValueError(
            "parse(): the pattern has no named groups — wrap the fields you want in "
            "(?P<name>...), e.g. r'(?P<level>[A-Z]+) (?P<message>.*)'"
        )
    casts = dict(cast or {})
    unknown = sorted(set(casts) - set(compiled.groupindex))
    if unknown:
        raise ValueError(
            f"parse(): cast refers to {unknown}, which are not named groups in the "
            f"pattern (groups: {sorted(compiled.groupindex)})"
        )

    return _scan(source, compiled, casts, encoding)


def _scan(
    source: str | os.PathLike[str] | Iterable[str],
    compiled: re.Pattern[str],
    casts: Mapping[str, Callable[[str], Any]],
    encoding: str,
) -> Iterator[dict[str, Any]]:
    with contextlib.ExitStack() as stack:
        if isinstance(source, (str, os.PathLike)):
            lines: Iterable[str] = stack.enter_context(open(source, encoding=encoding))
        else:
            lines = source
        for number, line in enumerate(lines, start=1):
            match = compiled.search(line.rstrip("\r\n"))
            if match is None:
                continue
            fields: dict[str, Any] = match.groupdict()
            for name, convert in casts.items():
                if fields[name] is None:
                    continue
                try:
                    fields[name] = convert(fields[name])
                except Exception as exc:
                    raise ValueError(
                        f"parse(): line {number}: cast for {name!r} failed on "
                        f"{fields[name]!r} ({exc}) — make the cast tolerant of this value "
                        "or tighten the pattern so the group only matches what it can convert"
                    ) from exc
            yield fields


_LOGFMT_PAIR = re.compile(r'([^\s="]+)(?:=("(?:[^"\\]|\\.)*"|[^\s"]*))?')
def parse_logfmt(line: str) -> dict[str, str]:
    """Split one logfmt line (`LogfmtFormatter`'s output, or any Heroku/Go-style `key=value` line)
    into a dict of strings. Quoted values are unescaped; a bare key with no `=` maps to `""`; if a
    key repeats, the last value wins. Values are always strings: convert them yourself, or pass a
    line through `parse()` with a pattern if you need typed fields."""
    return _native.parse_logfmt(line)  # type: ignore[no-any-return]
