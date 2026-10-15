from __future__ import annotations

import json
import urllib.request
from typing import Any

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
        body = json.dumps({"text": _format_message(record, occurrences)}).encode("utf-8")
        request = urllib.request.Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            if response.status >= 400:
                raise RuntimeError(
                    f"SlackAlertPlugin: webhook returned HTTP {response.status} — "
                    "check the webhook URL is still valid in Slack's app config"
                )


def _format_message(record: LogRecord, occurrences: int) -> str:
    suffix = f" (x{occurrences})" if occurrences > 1 else ""
    return f"[{record['level']}] {record['logger']}: {record['message']}{suffix}"
