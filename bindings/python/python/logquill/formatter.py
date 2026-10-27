"""Compatibility alias: the formatters now live in `logquill.formatters`.

Kept so `from logquill.formatter import Formatter, JSONFormatter` — the
import path used since 1.0.0 — keeps working.
"""

from logquill.formatters import Formatter, JSONFormatter

__all__ = ["Formatter", "JSONFormatter"]
