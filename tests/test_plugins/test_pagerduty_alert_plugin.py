from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from logquill.core.levels import Level
from logquill.core.records import create_record
from logquill.plugins.pagerduty_alert_plugin import PagerDutyAlertPlugin


def _record(level: Level = Level.ERROR, message: str = "boom") -> object:
    return create_record(level=level, logger="app.test", message=message, meta={"user_id": 42})


def test_send_alert_posts_events_api_v2_payload() -> None:
    plugin = PagerDutyAlertPlugin("routing-key-123")
    fake_response = MagicMock()
    fake_response.status = 202
    fake_response.__enter__.return_value = fake_response

    with patch("urllib.request.urlopen", return_value=fake_response) as mock_urlopen:
        plugin.send_alert(_record(), 1)  # type: ignore[arg-type]

    request = mock_urlopen.call_args[0][0]
    assert request.full_url == "https://events.pagerduty.com/v2/enqueue"
    body = json.loads(request.data)
    assert body["routing_key"] == "routing-key-123"
    assert body["event_action"] == "trigger"
    assert body["payload"]["severity"] == "error"
    assert body["payload"]["custom_details"]["user_id"] == 42


def test_summary_includes_occurrence_count_when_greater_than_one() -> None:
    plugin = PagerDutyAlertPlugin("routing-key-123")
    fake_response = MagicMock()
    fake_response.status = 202
    fake_response.__enter__.return_value = fake_response

    with patch("urllib.request.urlopen", return_value=fake_response) as mock_urlopen:
        plugin.send_alert(_record(), 6)  # type: ignore[arg-type]

    body = json.loads(mock_urlopen.call_args[0][0].data)
    assert "x6" in body["payload"]["summary"]


def test_fatal_level_maps_to_critical_severity() -> None:
    plugin = PagerDutyAlertPlugin("routing-key-123")
    fake_response = MagicMock()
    fake_response.status = 202
    fake_response.__enter__.return_value = fake_response

    with patch("urllib.request.urlopen", return_value=fake_response) as mock_urlopen:
        plugin.send_alert(_record(level=Level.FATAL), 1)  # type: ignore[arg-type]

    body = json.loads(mock_urlopen.call_args[0][0].data)
    assert body["payload"]["severity"] == "critical"


def test_send_alert_raises_on_http_error_status() -> None:
    plugin = PagerDutyAlertPlugin("routing-key-123")
    fake_response = MagicMock()
    fake_response.status = 400
    fake_response.__enter__.return_value = fake_response

    with patch("urllib.request.urlopen", return_value=fake_response):
        try:
            plugin.send_alert(_record(), 1)  # type: ignore[arg-type]
            raised = False
        except RuntimeError:
            raised = True

    assert raised
