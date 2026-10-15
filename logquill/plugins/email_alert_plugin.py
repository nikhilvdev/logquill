from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Any

from logquill.core.records import LogRecord
from logquill.plugins.alerting_plugin import AlertingPlugin


class EmailAlertPlugin(AlertingPlugin):
    """Sends deduplicated `AlertingPlugin` alerts by email over SMTP.

    Uses stdlib `smtplib`/`email` — no extra dependency required. Set
    `use_tls=False` for an SMTP server that doesn't support STARTTLS (e.g.
    a local relay); `username`/`password` are only used if both are set.
    """

    def __init__(
        self,
        *,
        smtp_host: str,
        smtp_port: int,
        from_addr: str,
        to_addrs: list[str],
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = True,
        timeout: float = 10.0,
        **kwargs: Any,
    ) -> None:
        """`kwargs` are forwarded to `AlertingPlugin.__init__` (`threshold`,
        `dedupe_window_seconds`, etc.)."""
        super().__init__(**kwargs)
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.from_addr = from_addr
        self.to_addrs = to_addrs
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.timeout = timeout

    def send_alert(self, record: LogRecord, occurrences: int) -> None:
        """Sends one plaintext email summarizing `record`, opening a fresh
        SMTP connection per alert (never raises to the caller — see
        `AlertingPlugin`'s `_safe_send` wrapper)."""
        message = EmailMessage()
        subject = f"[{record['level']}] {record['logger']}"
        if occurrences > 1:
            subject += f" (x{occurrences})"
        message["Subject"] = subject
        message["From"] = self.from_addr
        message["To"] = ", ".join(self.to_addrs)
        message.set_content(
            f"{record['message']}\n\n"
            f"occurrences: {occurrences}\n"
            f"timestamp: {record['timestamp']}\n"
            f"meta: {record['meta']!r}"
        )

        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self.timeout) as client:
            if self.use_tls:
                client.starttls()
            if self.username and self.password:
                client.login(self.username, self.password)
            client.send_message(message)
