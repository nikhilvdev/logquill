from __future__ import annotations

import json
import urllib.request
from typing import Any

from logquill.core.records import LogRecord
from logquill.plugins.alerting_plugin import AlertingPlugin

_ENDPOINT = "https://events.pagerduty.com/v2/enqueue"
_SEVERITY = {"ERROR": "error", "FATAL": "critical"}


class PagerDutyAlertPlugin(AlertingPlugin):
    """Sends deduplicated `AlertingPlugin` alerts to PagerDuty via the
    Events API v2 (`POST https://events.pagerduty.com/v2/enqueue`).

    `routing_key` is an Events API v2 integration key from a PagerDuty
    service. Uses stdlib `urllib` — no extra dependency required.
    """

    def __init__(self, routing_key: str, *, timeout: float = 5.0, **kwargs: Any) -> None:
        """`kwargs` are forwarded to `AlertingPlugin.__init__` (`threshold`,
        `dedupe_window_seconds`, etc.)."""
        super().__init__(**kwargs)
        self.routing_key = routing_key
        self.timeout = timeout

    def send_alert(self, record: LogRecord, occurrences: int) -> None:
        """POSTs one `trigger` event to PagerDuty's Events API v2; raises if
        the API responds with an error status (caught by `AlertingPlugin`'s
        `_safe_send` wrapper, so this never crashes the caller)."""
        summary = f"{record['logger']}: {record['message']}"
        if occurrences > 1:
            summary += f" (x{occurrences})"
        body = json.dumps(
            {
                "routing_key": self.routing_key,
                "event_action": "trigger",
                "payload": {
                    "summary": summary,
                    "severity": _SEVERITY.get(record["level"], "error"),
                    "source": record["logger"],
                    "timestamp": record["timestamp"],
                    "custom_details": {"occurrences": occurrences, **record["meta"]},
                },
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            _ENDPOINT,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            if response.status >= 400:
                raise RuntimeError(
                    f"PagerDutyAlertPlugin: Events API returned HTTP {response.status} — "
                    "check the routing key is a valid Events API v2 integration key"
                )
