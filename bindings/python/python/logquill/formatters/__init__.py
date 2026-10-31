from logquill.formatters.base import Formatter
from logquill.formatters.json_formatter import JSONFormatter
from logquill.formatters.logfmt_formatter import LogfmtFormatter
from logquill.formatters.text_formatter import TextFormatter, format_text


def native_formatter_name(formatter: Formatter | None) -> str | None:
    """The Rust formatter that does what `formatter` does (`"json"`, `"text"` or `"logfmt"`), or
    `None` for a custom formatter that only Python can run. `None` means the default (JSON)."""
    if formatter is None:
        return "json"
    return {JSONFormatter: "json", TextFormatter: "text", LogfmtFormatter: "logfmt"}.get(
        type(formatter)
    )


__all__ = [
    "Formatter",
    "JSONFormatter",
    "LogfmtFormatter",
    "TextFormatter",
    "format_text",
    "native_formatter_name",
]
