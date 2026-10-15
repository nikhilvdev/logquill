from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from logquill.core.levels import Level, parse_level
from logquill.core.records import LogRecord
from logquill.plugins.plugin import Plugin


class MetricsBackend(Protocol):
    """What `MetricsPlugin` needs from a metrics backend — implement this
    shape for your own (a fake in tests, or a system none of the built-in
    backends cover)."""

    def increment(self, name: str, *, tags: Mapping[str, str]) -> None:
        """Adds 1 to the named counter, tagged with `tags`."""
        ...

    def observe(self, name: str, value: float, *, tags: Mapping[str, str]) -> None:
        """Records one value into the named histogram, tagged with `tags`."""
        ...


class MetricsPlugin(Plugin):
    """Turns specific log patterns into metric emissions — the
    logs-to-metrics correlation the three-pillars observability model
    expects, done as an opt-in plugin rather than a required part of core.

    - Every record at `error_at` (default `ERROR`) or above increments a
      counter (`error_metric`, default `"logquill.errors"`), tagged by
      `logger`.
    - Any record carrying `meta.duration_ms` (every span-closing record,
      and anything else that happens to set it) feeds a histogram
      (`duration_metric`, default `"logquill.duration_ms"`), tagged by
      `logger`.

    Runs in `after_log`, after the record has already been dispatched to
    every transport — a slow or failing metrics backend can delay the next
    log call processed by this plugin, but never the one it's reporting on,
    and (like every plugin hook) a raising backend is caught by the
    pipeline and routed to `on_error`, never crashing the caller.

    `backend` defaults to `OTelMetricsBackend()` — the OTel Metrics SDK,
    the same optional dependency `OTLPTransport` already needs
    (`pip install logquill[otel]`), so this is additive, not a new one.
    Pass `StatsDBackend()`/`PrometheusPushgatewayBackend(...)` instead for
    a team not yet on OTel.
    """

    def __init__(
        self,
        backend: MetricsBackend | None = None,
        *,
        error_metric: str = "logquill.errors",
        duration_metric: str = "logquill.duration_ms",
        error_at: int | str | Level = Level.ERROR,
    ) -> None:
        """`backend` is constructed lazily (an `OTelMetricsBackend()`) if
        not given, so importing this module never imports the OTel SDK
        unless you actually use the default."""
        self._backend = backend
        self.error_metric = error_metric
        self.duration_metric = duration_metric
        self.error_at = parse_level(error_at)

    @property
    def backend(self) -> MetricsBackend:
        """The backend this plugin reports to — resolved on first use if
        none was given at construction."""
        if self._backend is None:
            from logquill.plugins.otel_metrics_backend import OTelMetricsBackend

            self._backend = OTelMetricsBackend()
        return self._backend

    def after_log(self, record: LogRecord) -> None:
        """Increments `error_metric` for a record at `error_at` or above;
        feeds `duration_metric` for a record carrying `meta.duration_ms`."""
        tags = {"logger": record["logger"]}

        if Level[record["level"]] >= self.error_at:
            self.backend.increment(self.error_metric, tags=tags)

        duration = record["meta"].get("duration_ms")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            self.backend.observe(self.duration_metric, float(duration), tags=tags)

    def close(self) -> None:
        """Closes the backend, if it has a `close()` method (e.g.
        `PrometheusPushgatewayBackend`, which flushes its accumulated
        metrics on close). Not called automatically by `Logger.close()` —
        call this yourself at shutdown, the same as `AlertingPlugin.close()`."""
        closer = getattr(self._backend, "close", None)
        if callable(closer):
            closer()
