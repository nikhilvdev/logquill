from __future__ import annotations

from typing import Any

from logquill import _native
from logquill.core.records import LogRecord
from logquill.plugins.alerting_plugin import AlertingPlugin

_ENDPOINT = "https://events.pagerduty.com/v2/enqueue"

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
        self._alert_sender().send(record, occurrences)

    def _native_sender(self) -> Any:
        return _native.pagerduty_sender(self.routing_key, self.timeout, _ENDPOINT)
