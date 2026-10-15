from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from logquill.plugins.otel_metrics_backend import OTelMetricsBackend


class FakeInstrument:
    def __init__(self) -> None:
        self.calls: list[tuple[float, dict[str, str]]] = []

    def add(self, value: float, attributes: dict[str, str]) -> None:
        self.calls.append((value, attributes))

    def record(self, value: float, attributes: dict[str, str]) -> None:
        self.calls.append((value, attributes))


class FakeMeter:
    def __init__(self) -> None:
        self.counters: dict[str, FakeInstrument] = {}
        self.histograms: dict[str, FakeInstrument] = {}

    def create_counter(self, name: str) -> FakeInstrument:
        instrument = FakeInstrument()
        self.counters[name] = instrument
        return instrument

    def create_histogram(self, name: str) -> FakeInstrument:
        instrument = FakeInstrument()
        self.histograms[name] = instrument
        return instrument


class FakeMeterProvider:
    def __init__(self) -> None:
        self.meter = FakeMeter()

    def get_meter(self, name: str) -> FakeMeter:
        return self.meter


@pytest.fixture
def fake_opentelemetry(monkeypatch: pytest.MonkeyPatch) -> Any:
    fake_metrics_module = types.ModuleType("opentelemetry.metrics")
    default_provider = FakeMeterProvider()
    fake_metrics_module.get_meter_provider = lambda: default_provider  # type: ignore[attr-defined]
    opentelemetry_module = types.ModuleType("opentelemetry")
    opentelemetry_module.metrics = fake_metrics_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "opentelemetry", opentelemetry_module)
    monkeypatch.setitem(sys.modules, "opentelemetry.metrics", fake_metrics_module)
    return default_provider


def test_increment_adds_one_to_a_lazily_created_counter(
    fake_opentelemetry: FakeMeterProvider,
) -> None:
    backend = OTelMetricsBackend()

    backend.increment("logquill.errors", tags={"logger": "app"})

    instrument = fake_opentelemetry.meter.counters["logquill.errors"]
    assert instrument.calls == [(1, {"logger": "app"})]


def test_observe_records_a_value_on_a_lazily_created_histogram(
    fake_opentelemetry: FakeMeterProvider,
) -> None:
    backend = OTelMetricsBackend()

    backend.observe("logquill.duration_ms", 12.5, tags={"logger": "app"})

    instrument = fake_opentelemetry.meter.histograms["logquill.duration_ms"]
    assert instrument.calls == [(12.5, {"logger": "app"})]


def test_the_same_instrument_is_reused_across_calls(fake_opentelemetry: FakeMeterProvider) -> None:
    backend = OTelMetricsBackend()

    backend.increment("logquill.errors", tags={})
    backend.increment("logquill.errors", tags={})

    assert len(fake_opentelemetry.meter.counters) == 1
    assert fake_opentelemetry.meter.counters["logquill.errors"].calls == [
        (1, {}),
        (1, {}),
    ]


def test_an_explicit_meter_provider_overrides_the_global_default(
    fake_opentelemetry: FakeMeterProvider,
) -> None:
    explicit_provider = FakeMeterProvider()

    backend = OTelMetricsBackend(meter_provider=explicit_provider)
    backend.increment("logquill.errors", tags={})

    assert "logquill.errors" in explicit_provider.meter.counters
    assert fake_opentelemetry.meter.counters == {}


def test_a_missing_opentelemetry_package_gives_an_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    monkeypatch.setitem(sys.modules, "opentelemetry.metrics", None)

    with pytest.raises(ImportError, match=r"pip install logquill\[otel\]"):
        OTelMetricsBackend()
