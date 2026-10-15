from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class OTelMetricsBackend:
    """`MetricsPlugin`'s default backend: records through the OpenTelemetry
    Metrics SDK, the same optional dependency `OTLPTransport` already needs
    (`pip install logquill[otel]`) — this is additive, not a new one.

    Lazily creates one `Counter`/`Histogram` instrument per metric name on
    first use (the SDK requires creating an instrument before recording to
    it, and there's no way to know every name a caller will ever pass in
    advance), then just calls `.add()`/`.record()` — the SDK's own
    `MeterProvider` and whatever reader/exporter it's configured with
    (typically the OTLP Metrics exporter) controls batching and export,
    the same as every other OTel instrument in a process.
    """

    def __init__(self, *, meter_name: str = "logquill", meter_provider: Any = None) -> None:
        """`meter_provider` defaults to whatever
        `opentelemetry.metrics.get_meter_provider()` currently returns —
        usually set once, globally, at process startup; pass one explicitly
        to scope this backend to its own provider instead (tests, or more
        than one in the same process)."""
        try:
            from opentelemetry import metrics
        except ImportError as exc:
            raise ImportError(
                "OTelMetricsBackend requires the OpenTelemetry SDK — install with "
                "`pip install logquill[otel]`."
            ) from exc
        provider = meter_provider if meter_provider is not None else metrics.get_meter_provider()
        self._meter = provider.get_meter(meter_name)
        self._counters: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}

    def _counter(self, name: str) -> Any:
        counter = self._counters.get(name)
        if counter is None:
            counter = self._meter.create_counter(name)
            self._counters[name] = counter
        return counter

    def _histogram(self, name: str) -> Any:
        histogram = self._histograms.get(name)
        if histogram is None:
            histogram = self._meter.create_histogram(name)
            self._histograms[name] = histogram
        return histogram

    def increment(self, name: str, *, tags: Mapping[str, str]) -> None:
        """Adds 1 to the named counter."""
        self._counter(name).add(1, attributes=dict(tags))

    def observe(self, name: str, value: float, *, tags: Mapping[str, str]) -> None:
        """Records one value into the named histogram."""
        self._histogram(name).record(value, attributes=dict(tags))
