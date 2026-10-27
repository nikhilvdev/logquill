from __future__ import annotations

import logging
import threading
import urllib.request
from typing import Callable, Literal, Sequence

from logquill import _native
from logquill.core.records import LogRecord
from logquill.formatters import Formatter, native_formatter_name
from logquill.transports.transport import Transport

Sender = Callable[[str, Sequence[str]], None]

_logger = logging.getLogger("logquill")


def _urllib_sender(timeout: float) -> Sender:
    def send(url: str, batch: Sequence[str]) -> None:
        body = "\n".join(batch).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/x-ndjson"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            response.read()

    return send


class HTTPTransport(Transport):
    """Batches formatted records and POSTs them as newline-delimited JSON.

    Uses `urllib` (stdlib) by default so the core package stays
    dependency-free. `backend="aiohttp"` (`pip install logquill[http]`) sends
    over a reused keep-alive connection instead — see `AiohttpSender`. Pass
    `sender` to swap in a fake for tests, or any other backend.

    The buffer is bounded by both `batch_size` records and `max_bytes` of
    formatted text — a flush fires as soon as either is reached — so a few
    huge records can't grow it without limit. A failed send is logged and
    that batch is dropped, never raised into the code that logged; the
    transport keeps accepting records, so a down endpoint costs completeness,
    not the process.

    `write()`/`flush()` lock around the buffer swap, the same as
    `BatchingTransport` — with `async_dispatch=False` (the default), a
    `Logger` shared across application threads would otherwise race on the
    buffer here too.
    """

    def __init__(
        self,
        url: str,
        *,
        formatter: Formatter | None = None,
        batch_size: int = 50,
        max_bytes: int = 1_000_000,
        timeout: float = 10.0,
        backend: Literal["urllib", "aiohttp"] = "urllib",
        sender: Sender | None = None,
    ) -> None:
        """`sender` overrides `backend` when given. `timeout` (seconds) bounds
        each request for the built-in backends; it's ignored when you pass
        your own `sender`. Raises `ValueError` for an unknown `backend`, and
        `ImportError` for `backend="aiohttp"` without `aiohttp` installed."""
        super().__init__(formatter)
        self.url = url
        self.batch_size = batch_size
        self.max_bytes = max_bytes
        self._sender: Sender
        if sender is not None:
            self._sender = sender
        elif backend == "urllib":
            self._sender = _urllib_sender(timeout)
        elif backend == "aiohttp":
            from logquill.transports.aiohttp_sender import AiohttpSender

            self._sender = AiohttpSender(timeout=timeout)
        else:
            raise ValueError(
                f"HTTPTransport: backend must be 'urllib' or 'aiohttp', got {backend!r}"
            )
        self._batch: list[str] = []
        self._batch_bytes = 0
        self._batch_lock = threading.Lock()
        # The default urllib backend is done in Rust (batching, byte bound, timeout, a dropped
        # batch reported and never raised). A caller's own `sender`, or aiohttp, keeps the Python path.
        self._native = None
        self._native_dispatch = None
        if sender is None and backend == "urllib":
            self._native = _native.http_transport(
                url, native_formatter_name(formatter) or "json", batch_size, max_bytes, timeout
            )
            if native_formatter_name(formatter):
                self._native_dispatch = self._native

    def write(self, formatted: str, record: LogRecord) -> None:
        """Buffers `formatted` and triggers a `flush()` once `batch_size`
        records or `max_bytes` bytes are buffered."""
        if self._native is not None:
            self._native.write(formatted, record)
            return
        size = len(formatted.encode("utf-8", errors="replace"))
        with self._batch_lock:
            self._batch.append(formatted)
            self._batch_bytes += size
            should_flush = (
                len(self._batch) >= self.batch_size or self._batch_bytes >= self.max_bytes
            )
        if should_flush:
            self.flush()

    def flush(self) -> None:
        """Sends whatever is currently buffered via the sender, clearing the
        buffer first. No-op if nothing is buffered. A failed send is logged,
        not raised."""
        if self._native is not None:
            self._native.flush()
            return
        with self._batch_lock:
            if not self._batch:
                return
            batch, self._batch = self._batch, []
            self._batch_bytes = 0
        try:
            self._sender(self.url, batch)
        except Exception:
            _logger.exception(
                "HTTPTransport: couldn't deliver %d log record(s) to %s — check the URL "
                "is reachable and accepts POSTed NDJSON, or raise `timeout`; "
                "those records were dropped",
                len(batch),
                self.url,
            )

    def close(self) -> None:
        """Flushes any remaining buffered records and releases the backend's
        connection, if it holds one."""
        if self._native is not None:
            self._native.close()
            return
        self.flush()
        closer = getattr(self._sender, "close", None)
        if callable(closer):
            closer()
