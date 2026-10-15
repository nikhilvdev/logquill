from __future__ import annotations

import sys
import types
from collections.abc import Mapping

import pytest

from logquill import Logger, MetricsPlugin
from logquill.transports.transport import CollectingTransport


class FakeBackend:
    def __init__(self) -> None:
        self.increments: list[tuple[str, dict[str, str]]] = []
        self.observations: list[tuple[str, float, dict[str, str]]] = []
        self.closed = False

    def increment(self, name: str, *, tags: Mapping[str, str]) -> None:
        self.increments.append((name, dict(tags)))

    def observe(self, name: str, value: float, *, tags: Mapping[str, str]) -> None:
        self.observations.append((name, value, dict(tags)))

    def close(self) -> None:
        self.closed = True


def _logger(backend: FakeBackend, **kwargs: object) -> tuple[Logger, CollectingTransport]:
    sink = CollectingTransport()
    logger = Logger(
        "app.agent",
        transports=[sink],
        plugins=[MetricsPlugin(backend, **kwargs)],  # type: ignore[arg-type]
    )
    return logger, sink


def test_an_error_increments_the_error_counter_tagged_by_logger() -> None:
    backend = FakeBackend()
    logger, _ = _logger(backend)

    logger.error("boom")

    assert backend.increments == [("logquill.errors", {"logger": "app.agent"})]


def test_fatal_also_increments_the_error_counter() -> None:
    backend = FakeBackend()
    logger, _ = _logger(backend)

    logger.fatal("boom")

    assert backend.increments == [("logquill.errors", {"logger": "app.agent"})]


def test_info_and_warn_do_not_increment_the_error_counter_by_default() -> None:
    backend = FakeBackend()
    logger, _ = _logger(backend)

    logger.info("fine")
    logger.warn("careful")

    assert backend.increments == []


def test_error_at_is_configurable() -> None:
    backend = FakeBackend()
    logger, _ = _logger(backend, error_at="WARN")

    logger.warn("careful")

    assert backend.increments == [("logquill.errors", {"logger": "app.agent"})]


def test_a_duration_ms_feeds_the_histogram() -> None:
    backend = FakeBackend()
    logger, _ = _logger(backend)

    with logger.span("work", duration_ms=12.5):
        pass

    assert ("logquill.duration_ms", 12.5, {"logger": "app.agent"}) in backend.observations


def test_a_record_with_no_duration_ms_does_not_feed_the_histogram() -> None:
    backend = FakeBackend()
    logger, _ = _logger(backend)

    logger.info("no duration here")

    assert backend.observations == []


def test_an_error_record_with_a_duration_does_both() -> None:
    backend = FakeBackend()
    logger, _ = _logger(backend)

    try:
        with logger.span("work"):
            raise ValueError("boom")
    except ValueError:
        pass

    assert backend.increments == [("logquill.errors", {"logger": "app.agent"})]
    assert len(backend.observations) == 1


def test_metric_names_are_configurable() -> None:
    backend = FakeBackend()
    logger, _ = _logger(backend, error_metric="my.errors", duration_metric="my.duration")

    logger.error("boom")
    with logger.span("work", duration_ms=1.0):
        pass

    assert backend.increments == [("my.errors", {"logger": "app.agent"})]
    assert ("my.duration", 1.0, {"logger": "app.agent"}) in backend.observations


def test_a_raising_backend_does_not_crash_the_caller() -> None:
    class Broken:
        def increment(self, name: str, *, tags: Mapping[str, str]) -> None:
            raise RuntimeError("backend is down")

        def observe(self, name: str, value: float, *, tags: Mapping[str, str]) -> None:
            raise RuntimeError("backend is down")

    sink = CollectingTransport()
    logger = Logger("app", transports=[sink], plugins=[MetricsPlugin(Broken())])  # type: ignore[arg-type]

    record = logger.error("boom")

    assert record is not None  # the caller's own call still succeeded
    assert sink.records == [record]


def test_close_closes_the_backend_if_it_has_one() -> None:
    backend = FakeBackend()
    plugin = MetricsPlugin(backend)

    plugin.close()

    assert backend.closed is True


def test_close_is_safe_when_the_backend_has_no_close_method() -> None:
    class NoClose:
        def increment(self, name: str, *, tags: Mapping[str, str]) -> None:
            pass

        def observe(self, name: str, value: float, *, tags: Mapping[str, str]) -> None:
            pass

    plugin = MetricsPlugin(NoClose())  # type: ignore[arg-type]

    plugin.close()  # must not raise


def test_the_backend_defaults_to_otel_and_is_resolved_lazily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_meter = types.SimpleNamespace(
        create_counter=lambda name: types.SimpleNamespace(add=lambda *a, **k: None),
        create_histogram=lambda name: types.SimpleNamespace(record=lambda *a, **k: None),
    )
    fake_provider = types.SimpleNamespace(get_meter=lambda name: fake_meter)
    fake_metrics_module = types.ModuleType("opentelemetry.metrics")
    fake_metrics_module.get_meter_provider = lambda: fake_provider  # type: ignore[attr-defined]
    opentelemetry_module = types.ModuleType("opentelemetry")
    opentelemetry_module.metrics = fake_metrics_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "opentelemetry", opentelemetry_module)
    monkeypatch.setitem(sys.modules, "opentelemetry.metrics", fake_metrics_module)

    plugin = MetricsPlugin()
    assert plugin._backend is None  # not resolved yet
    logger = Logger("app", plugins=[plugin])
    logger.error("boom")
    assert plugin._backend is not None  # resolved on first use
