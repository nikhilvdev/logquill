from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any

from logquill.core.records import LogRecord
from logquill.plugins.alerting_plugin import AlertingPlugin

# Apprise's own notification types, which each service maps onto its native
# severity (a color, an emoji, a priority).
_NOTIFY_TYPES = {"WARN": "warning", "ERROR": "failure", "FATAL": "failure"}


class AppriseAlertPlugin(AlertingPlugin):
    """Sends deduplicated `AlertingPlugin` alerts through
    [Apprise](https://github.com/caronc/apprise), which speaks to 100+
    notification services (Discord, Telegram, Teams, ntfy, Matrix, SMS
    gateways, ...) from one URL each — so "can it alert to X?" is answered
    by Apprise's service list rather than a bespoke plugin per service.

        logger.use(AppriseAlertPlugin(["discord://webhook_id/webhook_token",
                                       "ntfy://my-topic"]))

    Requires the optional dependency: `pip install logquill[apprise]`.
    `SlackAlertPlugin` and `PagerDutyAlertPlugin` remain the better choice
    for those two services, where a purpose-built plugin can format richer
    messages than Apprise's generic title-and-body interface.

    Like every `AlertingPlugin`, alerts go out on a background thread and a
    failure (an unreachable service, a rejected credential) is routed to
    `on_error`, never raised into the code that logged.
    """

    def __init__(
        self,
        urls: str | Sequence[str],
        *,
        title: str | None = None,
        apprise_client: Any = None,
        **kwargs: Any,
    ) -> None:
        """`urls` is one Apprise service URL or a list of them. `title`
        overrides the default `"[LEVEL] logger"`. `apprise_client` is any
        object with Apprise's `notify(body=, title=, notify_type=)` method,
        for tests or a client you've configured yourself (then `urls` is
        ignored). `kwargs` go to `AlertingPlugin.__init__` (`threshold`,
        `dedupe_window_seconds`, ...). Raises `ValueError` if Apprise rejects
        a URL, so a typo fails at startup instead of on the first alert."""
        super().__init__(**kwargs)
        self.title = title
        self._notify_lock = threading.Lock()
        self._client: Any = apprise_client if apprise_client is not None else _build_client(urls)

    def send_alert(self, record: LogRecord, occurrences: int) -> None:
        """Sends one notification for `record` to every configured service;
        raises if Apprise reports that delivery failed (caught by
        `AlertingPlugin`'s `_safe_send` wrapper, so this never crashes the
        caller)."""
        body = record["message"] if occurrences <= 1 else f"{record['message']} (x{occurrences})"
        title = self.title or f"[{record['level']}] {record['logger']}"
        with self._notify_lock:
            delivered = self._client.notify(
                body=body,
                title=title,
                notify_type=_NOTIFY_TYPES.get(record["level"], "info"),
            )
        if not delivered:
            raise RuntimeError(
                "AppriseAlertPlugin: Apprise reported that at least one notification failed to "
                "send — check the service URLs and that the services are reachable; run "
                "`apprise -vvv -b test <url>` to see the underlying error"
            )


def _build_client(urls: str | Sequence[str]) -> Any:
    try:
        import apprise
    except ImportError as exc:
        raise ImportError(
            "AppriseAlertPlugin requires the optional `apprise` dependency — "
            "install with `pip install logquill[apprise]`."
        ) from exc
    client = apprise.Apprise()
    for url in [urls] if isinstance(urls, str) else urls:
        if not client.add(url):
            raise ValueError(
                f"AppriseAlertPlugin: Apprise doesn't recognize the service URL {url!r} — "
                "see https://github.com/caronc/apprise/wiki for each service's URL format"
            )
    return client
