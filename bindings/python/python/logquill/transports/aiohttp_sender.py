from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Sequence
from typing import Any


class AiohttpSender:
    """An `HTTPTransport` sender that POSTs batches with `aiohttp`
    (`pip install logquill[http]`) instead of `urllib`.

    The reason to choose it is connection reuse: one `aiohttp.ClientSession`
    lives for the transport's lifetime, so every batch after the first rides
    an already-open, keep-alive connection instead of paying a new TCP/TLS
    handshake — a real saving against an HTTPS collector that receives a
    batch every few seconds. The session runs on a private event loop in a
    background thread, so it works the same whether or not your application
    has an event loop of its own.

    Calling it still blocks until the POST completes (or fails), exactly as
    the `urllib` sender does — the non-blocking guarantee for a log call
    comes from `Logger(async_dispatch=True)`, not from the sender.
    """

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        """`timeout` bounds each request, in seconds. `headers` are sent on
        every request, on top of `Content-Type: application/x-ndjson`.
        Raises `ImportError` right away if `aiohttp` isn't installed, rather
        than on the first flush."""
        try:
            import aiohttp  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "HTTPTransport(backend='aiohttp') requires the optional `aiohttp` "
                "dependency — install with `pip install logquill[http]`."
            ) from exc
        self.timeout = timeout
        self._headers = {"Content-Type": "application/x-ndjson", **(headers or {})}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: Any = None

    def __call__(self, url: str, batch: Sequence[str]) -> None:
        """POSTs `batch` to `url` as newline-delimited JSON and waits for the
        response. Raises if the request fails or the server answers 4xx/5xx."""
        loop = self._ensure_loop()
        body = "\n".join(batch).encode("utf-8")
        future = asyncio.run_coroutine_threadsafe(self._post(url, body), loop)
        try:
            future.result(timeout=self.timeout + 5.0)
        except BaseException:
            future.cancel()
            raise

    def close(self) -> None:
        """Close the session and stop the background loop. Idempotent; a
        later call starts a fresh session."""
        with self._lock:
            loop, thread = self._loop, self._thread
            self._loop = self._thread = None
        if loop is None or thread is None:
            return
        if self._session is not None:
            closing = asyncio.run_coroutine_threadsafe(self._session.close(), loop)
            # shutting down; there's nothing useful to do with a failed close
            with contextlib.suppress(Exception):
                closing.result(timeout=5.0)
            self._session = None
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5.0)
        if not thread.is_alive():
            loop.close()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                thread = threading.Thread(
                    target=self._run_loop, args=(loop,), name="logquill-aiohttp", daemon=True
                )
                thread.start()
                self._loop, self._thread = loop, thread
            return self._loop

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    async def _post(self, url: str, body: bytes) -> None:
        import aiohttp

        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout), headers=self._headers
            )
        async with self._session.post(url, data=body) as response:
            response.raise_for_status()
            await response.read()
