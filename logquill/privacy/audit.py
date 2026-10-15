"""`AuditLogger` — a `Logger` preset bundling LogQuill's privacy and
integrity controls for an audit trail: content capture off, secret and PII
redaction on, and hash-chained records so the result can be checked for
tampering afterward.

Scope honesty, same as the rest of the plugin pipeline: none of this makes
a deployment "compliant" with anything on its own — it's a technical
control that commonly supports compliance work (SOC 2, HIPAA, PCI-DSS audit
trails all expect *something* like this), never a substitute for the
process and review compliance actually requires.
"""

from __future__ import annotations

from typing import Any

from logquill.core.levels import Level
from logquill.core.logger import Logger
from logquill.core.records import (
    LogRecord,  # noqa: F401 — resolves MiddlewareFunc's forward reference
)
from logquill.plugins.context_plugin import ContextPlugin
from logquill.plugins.pii_redact_plugin import PIIRedactPlugin
from logquill.plugins.plugin import MiddlewareFunc, Plugin
from logquill.plugins.redact_plugin import RedactPlugin
from logquill.plugins.tamper_evident_plugin import TamperEvidentPlugin
from logquill.privacy.privacy import ContentCapturePolicy
from logquill.transports.transport import Transport


class AuditLogger(Logger):
    """A `Logger` preset for an audit trail. Equivalent to:

        Logger(
            name,
            content_policy="off",
            plugins=[RedactPlugin(), PIIRedactPlugin(), TamperEvidentPlugin(), *extra_plugins],
        )

    plus `.sign_head(key)` and `.head_hash`, forwarded to the
    `TamperEvidentPlugin` instance this attaches — see that class, and
    `logquill.plugins.tamper_evident_plugin.verify_signed_chain`, for what
    to do with a signature and how `logquill verify` checks one.

    `content_policy` defaults to `"off"` here too — pass `"hash"` if an
    audit trail should at least be able to confirm two entries shared a
    prompt without storing it. `extra_plugins` run after the three above,
    in the order given, the same as any other `Logger`.
    """

    def __init__(
        self,
        name: str,
        *,
        level: int | str | Level = Level.INFO,
        transports: list[Transport] | None = None,
        content_policy: ContentCapturePolicy | str = "off",
        extra_plugins: list[Plugin | MiddlewareFunc] | None = None,
        **kwargs: Any,
    ) -> None:
        """`**kwargs` are forwarded to `Logger.__init__` (`async_dispatch`,
        `flush_at_exit`, etc.)."""
        self._tamper = TamperEvidentPlugin()
        plugins: list[Plugin | MiddlewareFunc] = [
            RedactPlugin(),
            PIIRedactPlugin(),
            self._tamper,
            *(extra_plugins or []),
        ]
        super().__init__(
            name,
            level=level,
            transports=transports,
            plugins=plugins,
            content_policy=content_policy,
            **kwargs,
        )

    def child(self, name: str, /, **fixed_meta: Any) -> AuditLogger:
        """Like `Logger.child()`, but returns another `AuditLogger` with
        the *same* redaction plugins and, importantly, the **same**
        `TamperEvidentPlugin` instance as this one — a child shares this
        logger's transports already (the same output stream), so it
        continues this logger's hash chain rather than starting a second,
        interleaved one that would make the combined file unverifiable as
        a single chain."""
        child_logger = AuditLogger(
            f"{self.name}.{name}",
            level=self._level,
            transports=self.transports,
            content_policy=self.content_policy,
        )
        child_logger._tamper = self._tamper
        child_logger.plugins = [RedactPlugin(), PIIRedactPlugin(), self._tamper]
        if fixed_meta:
            child_logger.use(ContextPlugin(**fixed_meta))
        child_logger._worker = self._worker
        return child_logger

    @property
    def head_hash(self) -> str:
        """This logger's current hash-chain head — see
        `TamperEvidentPlugin.head_hash`."""
        return self._tamper.head_hash

    def sign_head(self, key: bytes) -> str:
        """Signs `self.head_hash` with `key` — see
        `TamperEvidentPlugin.sign_head`/the module-level `sign_head` for
        what to do with the result and why it has to be kept somewhere
        other than this logger's own output."""
        return self._tamper.sign_head(key)
