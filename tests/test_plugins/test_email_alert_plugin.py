from __future__ import annotations

from unittest.mock import MagicMock, patch

from logquill.core.levels import Level
from logquill.core.records import create_record
from logquill.plugins.email_alert_plugin import EmailAlertPlugin


def _record(message: str = "boom") -> object:
    return create_record(level=Level.ERROR, logger="app.test", message=message, meta={})


def test_send_alert_sends_via_smtp_with_starttls_and_login() -> None:
    plugin = EmailAlertPlugin(
        smtp_host="smtp.example.com",
        smtp_port=587,
        from_addr="alerts@example.com",
        to_addrs=["oncall@example.com"],
        username="user",
        password="pass",
    )
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client

    with patch("smtplib.SMTP", return_value=fake_client) as mock_smtp:
        plugin.send_alert(_record(), 1)  # type: ignore[arg-type]

    mock_smtp.assert_called_once_with("smtp.example.com", 587, timeout=10.0)
    fake_client.starttls.assert_called_once()
    fake_client.login.assert_called_once_with("user", "pass")
    fake_client.send_message.assert_called_once()
    sent_message = fake_client.send_message.call_args[0][0]
    assert sent_message["To"] == "oncall@example.com"
    assert "boom" in sent_message.get_content()


def test_send_alert_skips_login_without_credentials() -> None:
    plugin = EmailAlertPlugin(
        smtp_host="smtp.example.com",
        smtp_port=25,
        from_addr="alerts@example.com",
        to_addrs=["oncall@example.com"],
        use_tls=False,
    )
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client

    with patch("smtplib.SMTP", return_value=fake_client):
        plugin.send_alert(_record(), 1)  # type: ignore[arg-type]

    fake_client.starttls.assert_not_called()
    fake_client.login.assert_not_called()


def test_subject_includes_occurrence_count_when_greater_than_one() -> None:
    plugin = EmailAlertPlugin(
        smtp_host="smtp.example.com",
        smtp_port=25,
        from_addr="alerts@example.com",
        to_addrs=["oncall@example.com"],
    )
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client

    with patch("smtplib.SMTP", return_value=fake_client):
        plugin.send_alert(_record(), 9)  # type: ignore[arg-type]

    sent_message = fake_client.send_message.call_args[0][0]
    assert "x9" in sent_message["Subject"]
