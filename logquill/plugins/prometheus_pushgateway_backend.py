from __future__ import annotations

import re
import threading
import urllib.request
from collections.abc import Mapping
from typing import Callable

_INVALID_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_:]")
_LEADING_DIGIT = re.compile(r"^[0-9]")


def _sanitize(name: str) -> str:
    sanitized = _INVALID_NAME_CHARS.sub("_", name)
    return f"_{sanitized}" if _LEADING_DIGIT.match(sanitized) else sanitized


def _labels(tags: tuple[tuple[str, str], ...]) -> str:
    if not tags:
        return ""
    pairs = ",".join(f'{key}="{value}"' for key, value in tags)
    return f"{{{pairs}}}"


class PrometheusPushgatewayBackend:
    """A `MetricsPlugin` backend for teams running a Prometheus Pushgateway
    instead of OTel — stdlib `urllib` only, no new dependency (not even
    `prometheus_client`).

    Pushgateway is designed around periodic batch pushes, not one HTTP
    request per metric — so `increment()`/`observe()` only accumulate
    in-memory counters/histogram sums; nothing reaches the network until
    `push()` is called (by hand, on your own timer, or via `close()`,
    which calls it once). This also means metrics recorded on the hot log
    path never pay for an HTTP round trip.

    Metric names are sanitized to Prometheus's `[a-zA-Z_:][a-zA-Z0-9_:]*`
    rule (e.g. `logquill.errors` becomes `logquill_errors`); `tags` become
    labels.
    """

    def __init__(
        self,
        gateway_url: str,
        *,
        job_name: str = "logquill",
        timeout: float = 5.0,
        sender: Callable[[str, bytes], None] | None = None,
    ) -> None:
        """`sender(url, body)` defaults to a stdlib `urllib`-based `PUT`
        (Pushgateway's "replace this job's metrics" semantics); override
        for a fake in tests."""
        self._gateway_url = gateway_url.rstrip("/")
        self._job_name = job_name
        self._timeout = timeout
        self._sender = sender or self._urllib_put
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histogram_sums: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histogram_counts: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}

    def _urllib_put(self, url: str, body: bytes) -> None:
        request = urllib.request.Request(url, data=body, method="PUT")
        with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
            if response.status >= 400:
                raise RuntimeError(
                    f"PrometheusPushgatewayBackend: pushgateway returned HTTP "
                    f"{response.status} — check the gateway URL is reachable and correct"
                )

    @staticmethod
    def _key(name: str, tags: Mapping[str, str]) -> tuple[str, tuple[tuple[str, str], ...]]:
        return name, tuple(sorted(tags.items()))

    def increment(self, name: str, *, tags: Mapping[str, str]) -> None:
        """Adds 1 to the named counter's in-memory total."""
        key = self._key(name, tags)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + 1

    def observe(self, name: str, value: float, *, tags: Mapping[str, str]) -> None:
        """Adds one sample to the named histogram's in-memory sum/count."""
        key = self._key(name, tags)
        with self._lock:
            self._histogram_sums[key] = self._histogram_sums.get(key, 0.0) + value
            self._histogram_counts[key] = self._histogram_counts.get(key, 0) + 1

    def _render(self) -> str:
        with self._lock:
            counters = dict(self._counters)
            sums = dict(self._histogram_sums)
            counts = dict(self._histogram_counts)

        lines = []
        for (name, tags), value in counters.items():
            lines.append(f"{_sanitize(name)}{_labels(tags)} {value}")
        for key, total in sums.items():
            name, tags = key
            metric = _sanitize(name)
            lines.append(f"{metric}_sum{_labels(tags)} {total}")
            lines.append(f"{metric}_count{_labels(tags)} {counts[key]}")
        return "\n".join(lines) + "\n" if lines else ""

    def push(self) -> None:
        """Sends every metric accumulated so far to the pushgateway, as one
        request — the in-memory totals are *not* cleared, since Pushgateway
        itself expects each push to carry the job's current cumulative
        state, the same way a `/metrics` scrape would."""
        body = self._render()
        if not body:
            return
        url = f"{self._gateway_url}/metrics/job/{self._job_name}"
        self._sender(url, body.encode("utf-8"))

    def close(self) -> None:
        """Pushes one final time."""
        self.push()
