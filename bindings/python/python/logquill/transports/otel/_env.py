"""Where an OTLP transport sends to, read the way the OpenTelemetry SDK reads it: an explicit
argument first, then `OTEL_EXPORTER_OTLP_<SIGNAL>_ENDPOINT`, then `OTEL_EXPORTER_OTLP_ENDPOINT` with
the signal's path added, else the local collector."""

from __future__ import annotations

import os
from urllib.parse import unquote


def endpoint(explicit: str | None, signal: str) -> str | None:
    """The URL to POST `signal` (`"traces"` or `"logs"`) to, or `None` for the native default."""
    if explicit:
        return explicit
    specific = os.environ.get(f"OTEL_EXPORTER_OTLP_{signal.upper()}_ENDPOINT")
    if specific:
        return specific
    general = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if general:
        return f"{general.rstrip('/')}/v1/{signal}"
    return None


def headers(explicit: dict[str, str] | None, signal: str) -> list[tuple[str, str]]:
    """The request headers: those in `OTEL_EXPORTER_OTLP_[<SIGNAL>_]HEADERS` (`k=v,k2=v2`), then the
    explicit ones, which win."""
    merged: dict[str, str] = {}
    for variable in ("OTEL_EXPORTER_OTLP_HEADERS", f"OTEL_EXPORTER_OTLP_{signal.upper()}_HEADERS"):
        for pair in os.environ.get(variable, "").split(","):
            name, separator, value = pair.partition("=")
            if separator and name.strip():
                merged[name.strip()] = unquote(value.strip())
    merged.update(explicit or {})
    return list(merged.items())
