from __future__ import annotations

import os
import socket
from typing import Callable

from logquill.core.levels import Level, parse_level
from logquill import _native
from logquill.core.records import LogRecord
from logquill.formatters import Formatter, native_formatter_name
from logquill.transports.transport import Transport

SyslogSender = Callable[[bytes], None]

_FACILITY_USER = 1

_SEVERITY_BY_LEVEL = {
    Level.TRACE: 7,  # debug
    Level.DEBUG: 7,  # debug
    Level.INFO: 6,  # informational
    Level.WARN: 4,  # warning
    Level.ERROR: 3,  # error
    Level.FATAL: 2,  # critical
}

_NIL = "-"


class SyslogTransport(Transport):
    """Sends each record as one RFC 5424 syslog message over UDP (default)
    or TCP — stdlib `socket` only, no dependency.

    `facility` follows the standard syslog facility codes (default `1`,
    `LOG_USER` — matches Python's own `logging.handlers.SysLogHandler`
    default). LogQuill's levels map onto syslog severities (`TRACE`/`DEBUG`
    -> debug, `INFO` -> informational, `WARN` -> warning, `ERROR` -> error,
    `FATAL` -> critical); LogQuill has no concept of syslog's more severe
    alert/emergency levels, so those are never emitted.

    Not a batching transport — syslog is a one-datagram/one-message-per-call
    protocol, unlike the batched HTTP-API transports elsewhere in this
    package. TCP messages are newline-framed per RFC 6587's "non-transparent
    framing" (the simpler, widely-supported option); UDP datagrams need no
    extra framing, since the datagram boundary already is the message
    boundary.

    Pass `sender` to inject a fake for tests or an alternate transport.
    """

    def __init__(
        self,
        *,
        host: str = "localhost",
        port: int = 514,
        protocol: str = "udp",
        facility: int = _FACILITY_USER,
        app_name: str | None = None,
        formatter: Formatter | None = None,
        sender: SyslogSender | None = None,
    ) -> None:
        """`protocol` must be `"udp"` or `"tcp"`; `sender` defaults to
        sending over a real socket, opened lazily on first use — inject a
        fake for tests."""
        super().__init__(formatter)
        if protocol not in ("udp", "tcp"):
            raise ValueError(f"SyslogTransport: protocol must be 'udp' or 'tcp', got {protocol!r}")
        self.host = host
        self.port = port
        self.protocol = protocol
        self.facility = facility
        self.app_name = app_name
        self._hostname = socket.gethostname()
        self._pid = os.getpid()
        self._injected_sender = sender
        self._sock: socket.socket | None = None
        if sender is None:
            # Done in Rust: RFC 5424 framing, the address looked up once, a write timeout, and a
            # pause after a failed connect instead of a slow retry for every record.
            self._native = _native.syslog(
                host, port, protocol, facility, app_name, native_formatter_name(formatter) or "json"
            )
            self._native_dispatch = self._native if native_formatter_name(formatter) else None

    def _resolved_sender(self) -> SyslogSender:
        if self._injected_sender is not None:
            return self._injected_sender
        if self._sock is None:
            sock_type = socket.SOCK_DGRAM if self.protocol == "udp" else socket.SOCK_STREAM
            sock = socket.socket(socket.AF_INET, sock_type)
            if self.protocol == "tcp":
                sock.connect((self.host, self.port))
            self._sock = sock
        return self._send_via_socket

    def _send_via_socket(self, data: bytes) -> None:
        assert self._sock is not None
        if self.protocol == "udp":
            self._sock.sendto(data, (self.host, self.port))
        else:
            self._sock.sendall(data)

    def write(self, formatted: str, record: LogRecord) -> None:
        """Frames `formatted` as one RFC 5424 message and sends it
        immediately (newline-terminated for TCP, as a single datagram for
        UDP) — never batched, unlike the HTTP-API transports."""
        if self._native is not None:
            self._native.write(formatted, record)
            return
        severity = _SEVERITY_BY_LEVEL.get(parse_level(record["level"]), 6)
        pri = self.facility * 8 + severity
        app_name = self.app_name or record["logger"]
        message = (
            f"<{pri}>1 {record['timestamp']} {self._hostname} {app_name} "
            f"{self._pid} {_NIL} {_NIL} {formatted}"
        )
        data = message.encode("utf-8")
        if self.protocol == "tcp":
            data += b"\n"
        self._resolved_sender()(data)

    def close(self) -> None:
        """Closes the underlying socket, if one was opened."""
        if self._native is not None:
            self._native.close()
            return
        if self._sock is not None:
            self._sock.close()
            self._sock = None
