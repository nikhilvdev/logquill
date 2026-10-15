from __future__ import annotations

from collections.abc import Iterable

from logquill.core.records import LogRecord
from logquill.plugins.plugin import Plugin
from logquill.privacy.privacy import FieldClass, keys_in_class

#: The `secret`-classed keys in `logquill.privacy.FIELD_CLASSES` — kept as its
#: own name since it's also `RedactPlugin`'s default, and existing code may
#: already import it directly.
DEFAULT_REDACTED_KEYS = frozenset(keys_in_class("secret"))


class RedactPlugin(Plugin):
    """Replaces sensitive `meta` values, matched by key (case-insensitive), with a placeholder."""

    def __init__(
        self,
        keys: Iterable[str] = DEFAULT_REDACTED_KEYS,
        replacement: str = "***",
        *,
        classes: Iterable[FieldClass] | None = None,
    ) -> None:
        """`keys` defaults to `DEFAULT_REDACTED_KEYS`; matching is
        case-insensitive, so callers don't need to worry about casing.
        `classes`, if given, adds every key `logquill.privacy.FIELD_CLASSES`
        tags with one of the given classes — `RedactPlugin(keys=(),
        classes=["secret", "content"])` redacts both the usual credential
        keys and every content field, by class rather than listing each one.
        """
        self.keys = {key.lower() for key in keys}
        for field_class in classes or ():
            self.keys |= {key.lower() for key in keys_in_class(field_class)}
        self.replacement = replacement

    def before_log(self, record: LogRecord) -> LogRecord | None:
        """Replaces the value of any `meta` key matching `keys`
        (case-insensitively) with `replacement`."""
        meta = record["meta"]
        record["meta"] = {
            key: self.replacement if key.lower() in self.keys else value
            for key, value in meta.items()
        }
        return record

    def redact_local(self, name: str, text: str) -> str:
        """Masks a local variable captured by `diagnose=True` when its name
        matches `keys` (case-insensitively) — the same rule `before_log`
        applies to `meta` keys, so `password`, `token` and friends never
        reach a traceback either."""
        return self.replacement if name.lower() in self.keys else text
