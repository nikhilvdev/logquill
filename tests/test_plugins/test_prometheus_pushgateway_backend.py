from __future__ import annotations

from logquill.plugins.prometheus_pushgateway_backend import PrometheusPushgatewayBackend, _sanitize


def test_sanitize_replaces_invalid_characters_with_underscores() -> None:
    assert _sanitize("logquill.errors") == "logquill_errors"


def test_sanitize_prefixes_a_leading_digit() -> None:
    assert _sanitize("2xx.count") == "_2xx_count"


def test_increment_and_observe_do_not_send_anything_until_push() -> None:
    sent: list[tuple[str, str]] = []
    backend = PrometheusPushgatewayBackend(
        "http://localhost:9091", sender=lambda url, body: sent.append((url, body.decode()))
    )

    backend.increment("logquill.errors", tags={"logger": "app"})
    backend.observe("logquill.duration_ms", 10.0, tags={"logger": "app"})

    assert sent == []


def test_push_sends_a_put_to_the_job_url_with_rendered_metrics() -> None:
    sent: list[tuple[str, str]] = []
    backend = PrometheusPushgatewayBackend(
        "http://localhost:9091",
        job_name="myjob",
        sender=lambda url, body: sent.append((url, body.decode())),
    )

    backend.increment("logquill.errors", tags={"logger": "app"})
    backend.push()

    ((url, body),) = sent
    assert url == "http://localhost:9091/metrics/job/myjob"
    assert body == 'logquill_errors{logger="app"} 1.0\n'


def test_repeated_increments_accumulate_into_one_cumulative_total() -> None:
    sent: list[tuple[str, str]] = []
    backend = PrometheusPushgatewayBackend(
        "http://localhost:9091", sender=lambda url, body: sent.append((url, body.decode()))
    )

    backend.increment("logquill.errors", tags={"logger": "app"})
    backend.increment("logquill.errors", tags={"logger": "app"})
    backend.increment("logquill.errors", tags={"logger": "app"})
    backend.push()

    (_url, body) = sent[0]
    assert body == 'logquill_errors{logger="app"} 3.0\n'


def test_observe_accumulates_a_histogram_sum_and_count() -> None:
    sent: list[tuple[str, str]] = []
    backend = PrometheusPushgatewayBackend(
        "http://localhost:9091", sender=lambda url, body: sent.append((url, body.decode()))
    )

    backend.observe("logquill.duration_ms", 10.0, tags={"logger": "app"})
    backend.observe("logquill.duration_ms", 20.0, tags={"logger": "app"})
    backend.push()

    (_url, body) = sent[0]
    lines = body.splitlines()
    assert 'logquill_duration_ms_sum{logger="app"} 30.0' in lines
    assert 'logquill_duration_ms_count{logger="app"} 2' in lines


def test_different_tag_sets_are_tracked_as_separate_series() -> None:
    sent: list[tuple[str, str]] = []
    backend = PrometheusPushgatewayBackend(
        "http://localhost:9091", sender=lambda url, body: sent.append((url, body.decode()))
    )

    backend.increment("logquill.errors", tags={"logger": "app.a"})
    backend.increment("logquill.errors", tags={"logger": "app.b"})
    backend.push()

    (_url, body) = sent[0]
    assert 'logquill_errors{logger="app.a"} 1.0' in body
    assert 'logquill_errors{logger="app.b"} 1.0' in body


def test_pushing_with_nothing_accumulated_sends_nothing() -> None:
    sent: list[tuple[str, str]] = []
    backend = PrometheusPushgatewayBackend(
        "http://localhost:9091", sender=lambda url, body: sent.append((url, body.decode()))
    )

    backend.push()

    assert sent == []


def test_close_pushes_exactly_once() -> None:
    sent: list[tuple[str, str]] = []
    backend = PrometheusPushgatewayBackend(
        "http://localhost:9091", sender=lambda url, body: sent.append((url, body.decode()))
    )

    backend.increment("logquill.errors", tags={})
    backend.close()

    assert len(sent) == 1
