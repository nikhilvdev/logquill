from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from logquill.core.levels import Level
from logquill.core.records import create_record
from logquill.plugins.slack_alert_plugin import SlackAlertPlugin


def _record(message: str = "boom") -> object:
    return create_record(level=Level.ERROR, logger="app.test", message=message, meta={})


def test_send_alert_posts_json_body_with_text() -> None:
    plugin = SlackAlertPlugin("https://hooks.slack.example/T000/B000/xxx")
    fake_response = MagicMock()
    fake_response.status = 200
    fake_response.__enter__.return_value = fake_response

    with patch("urllib.request.urlopen", return_value=fake_response) as mock_urlopen:
        plugin.send_alert(_record(), 1)  # type: ignore[arg-type]

    request = mock_urlopen.call_args[0][0]
    assert request.full_url == "https://hooks.slack.example/T000/B000/xxx"
    body = json.loads(request.data)
    assert "boom" in body["text"]


def test_send_alert_includes_occurrence_count_when_greater_than_one() -> None:
    plugin = SlackAlertPlugin("https://hooks.slack.example/T000/B000/xxx")
    fake_response = MagicMock()
    fake_response.status = 200
    fake_response.__enter__.return_value = fake_response

    with patch("urllib.request.urlopen", return_value=fake_response) as mock_urlopen:
        plugin.send_alert(_record(), 7)  # type: ignore[arg-type]

    body = json.loads(mock_urlopen.call_args[0][0].data)
    assert "x7" in body["text"]


def test_send_alert_raises_on_http_error_status() -> None:
    plugin = SlackAlertPlugin("https://hooks.slack.example/T000/B000/xxx")
    fake_response = MagicMock()
    fake_response.status = 500
    fake_response.__enter__.return_value = fake_response

    with patch("urllib.request.urlopen", return_value=fake_response):
        try:
            plugin.send_alert(_record(), 1)  # type: ignore[arg-type]
            raised = False
        except RuntimeError:
            raised = True

    assert raised
