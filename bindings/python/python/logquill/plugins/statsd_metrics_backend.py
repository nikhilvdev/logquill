from __future__ import annotations

import logging
import socket
from collections.abc import Mapping
from typing import Protocol

from logquill import _native

_logger = logging.getLogger("logquill")


class UDPSocketLike(Protocol):
    """The subset of a `socket.socket` this backend calls — implement this
    shape to inject a fake in tests without sending real UDP packets."""

    def sendto(self, data: bytes, address: tuple[str, int]) -> int:
        """Sends `data` to `address`."""
        ...

    def close(self) -> None:
        """Releases the socket."""
        ...


class StatsDBackend:
    """A `MetricsPlugin` backend for StatsD (or a DogStatsD-compatible
    agent) — stdlib `socket` only, no new dependency.

    Sends the StatsD wire format over UDP: `name:value|type`, with tags
    appended as `|#key:value,key:value` — the DogStatsD extension, since
    plain StatsD has no tag concept at all and this extension is the
    closest thing to a de facto standard (Datadog's agent, Telegraf, and
    most StatsD-compatible collectors understand it; a collector that
    doesn't just ignores the trailing segment it doesn't recognize).

    UDP sends never block waiting for an ack and are fire-and-forget by
    design — a send that fails outright (no route, closed socket) is
    logged and dropped, never raised into the caller.
    """

    def __init__(
        self,
        *,
        host: str = "localhost",
        port: int = 8125,
        prefix: str | None = None,
        sock: UDPSocketLike | None = None,
    ) -> None:
        """`prefix`, if given, is prepended to every metric name as
        `f"{prefix}.{name}"`. `sock` is injectable for tests; defaults to a
        real UDP `socket.socket`."""
        self._host = host
        self._port = port
        self._prefix = prefix
        # With no injected socket the datagrams are sent from Rust: the host is looked up once and
        # a failed send is dropped. A `sock` you pass (for a test) keeps the Python path.
        self._native = _native.StatsdBackend(host, port, prefix) if sock is None else None
        self._sock: UDPSocketLike | None = (
            sock if sock is not None else None
        )

    def _full_name(self, name: str) -> str:
        return f"{self._prefix}.{name}" if self._prefix else name

    def _tag_suffix(self, tags: Mapping[str, str]) -> str:
        if not tags:
            return ""
        return "|#" + ",".join(f"{key}:{value}" for key, value in tags.items())

    def _send(self, line: str) -> None:
        assert self._sock is not None
        try:
            self._sock.sendto(line.encode("utf-8"), (self._host, self._port))
        except OSError:
            _logger.debug("StatsDBackend: failed to send a metric to %s:%d", self._host, self._port)

    def increment(self, name: str, *, tags: Mapping[str, str]) -> None:
        """Sends a StatsD counter increment (`|c`) of 1."""
        if self._native is not None:
            self._native.increment(name, list(tags.items()))
            return
        self._send(f"{self._full_name(name)}:1|c{self._tag_suffix(tags)}")

    def observe(self, name: str, value: float, *, tags: Mapping[str, str]) -> None:
        """Sends a StatsD histogram sample (`|h`)."""
        if self._native is not None:
            self._native.observe(name, value, list(tags.items()))
            return
        self._send(f"{self._full_name(name)}:{value}|h{self._tag_suffix(tags)}")

    def close(self) -> None:
        """Releases the underlying socket."""
        if self._sock is not None:
            self._sock.close()
