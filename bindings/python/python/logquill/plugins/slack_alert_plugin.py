from __future__ import annotations

from typing import Any

from logquill import _native
from logquill.core.records import LogRecord
from logquill.plugins.alerting_plugin import AlertingPlugin


class SlackAlertPlugin(AlertingPlugin):
    """Sends deduplicated `AlertingPlugin` alerts to a Slack incoming webhook.

    `webhook_url` is the full "Incoming Webhook" URL from Slack's app
    config. Uses stdlib `urllib` — no extra dependency required.
    """

    def __init__(self, webhook_url: str, *, timeout: float = 5.0, **kwargs: Any) -> None:
        """`kwargs` are forwarded to `AlertingPlugin.__init__` (`threshold`,
        `dedupe_window_seconds`, etc.)."""
        super().__init__(**kwargs)
        self.webhook_url = webhook_url
        self.timeout = timeout

    def send_alert(self, record: LogRecord, occurrences: int) -> None:
        """POSTs a plain-text summary of `record` to the Slack webhook;
        raises if Slack responds with an error status (caught by
        `AlertingPlugin`'s `_safe_send` wrapper, so this never crashes the
        caller)."""
        self._alert_sender().send(record, occurrences)

    def _native_sender(self) -> Any:
        return _native.slack_sender(self.webhook_url, self.timeout)
