from __future__ import annotations

from logquill import _native
from logquill.core.records import LogRecord


class LogfmtFormatter:
    """Single-line `key=value` output, the logfmt convention of Heroku and Go's logging ecosystem,
    for teams whose downstream tooling (Loki, Splunk, `grep`) expects it:

        timestamp=2026-01-01T00:00:00.000Z level=INFO logger=app message="user signed up" user_id=42

    An LLM call's `llm` block follows as `llm.model=...`, `llm.tokens_in=...`. `meta` keys are
    emitted as top-level pairs after the four record fields; nested dicts flatten to dotted keys
    (`http.status=200`, five levels deep at most); lists are emitted as a JSON string. Values with
    whitespace, `=`, quotes or control characters are double-quoted and escaped, so the output is
    always exactly one line. `logquill.parse_logfmt` reads the format back. Never raises on odd
    `meta` values.
    """

    def format(self, record: LogRecord) -> str:
        """Renders `record` as one logfmt line."""
        return _native.format_logfmt(record)
