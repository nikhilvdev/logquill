import logging
import sys
from typing import Sequence

import pytest

from logquill.core.logger import Logger
from logquill.transports.http_transport import HTTPTransport


class FakeSender:
    """Fake sink standing in for the network call, so tests never hit the wire."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Sequence[str]]] = []

    def __call__(self, url: str, batch: Sequence[str]) -> None:
        self.calls.append((url, list(batch)))


def test_batches_until_batch_size_is_reached() -> None:
    sender = FakeSender()
    transport = HTTPTransport("https://example.com/logs", batch_size=2, sender=sender)
    logger = Logger("app.test", transports=[transport])

    logger.info("one")
    assert sender.calls == []

    logger.info("two")
    assert len(sender.calls) == 1
    assert len(sender.calls[0][1]) == 2


def test_close_flushes_a_partial_batch() -> None:
    sender = FakeSender()
    transport = HTTPTransport("https://example.com/logs", batch_size=10, sender=sender)
    logger = Logger("app.test", transports=[transport])

    logger.info("only one")
    logger.close()

    assert len(sender.calls) == 1
    assert len(sender.calls[0][1]) == 1


def test_close_on_empty_batch_sends_nothing() -> None:
    sender = FakeSender()
    transport = HTTPTransport("https://example.com/logs", sender=sender)

    transport.close()

    assert sender.calls == []


def test_flushes_early_when_the_buffered_bytes_reach_max_bytes() -> None:
    sender = FakeSender()
    transport = HTTPTransport(
        "https://example.com/logs", batch_size=1000, max_bytes=100, sender=sender
    )
    logger = Logger("app.test", transports=[transport])

    logger.info("big", blob="x" * 200)

    assert len(sender.calls) == 1


def test_a_failing_sender_is_logged_not_raised_and_later_records_still_flow(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[int] = []

    def flaky(url: str, batch: Sequence[str]) -> None:
        calls.append(len(batch))
        if len(calls) == 1:
            raise OSError("connection refused")

    transport = HTTPTransport("https://example.com/logs", batch_size=1, sender=flaky)

    with caplog.at_level(logging.ERROR, logger="logquill"):
        transport.write("first", None)  # type: ignore[arg-type]
        transport.write("second", None)  # type: ignore[arg-type]
        transport.close()

    assert calls == [1, 1]
    assert "couldn't deliver 1 log record(s) to https://example.com/logs" in caplog.text


def test_close_releases_a_sender_that_holds_a_connection() -> None:
    class ClosableSender(FakeSender):
        closed = False

        def close(self) -> None:
            self.closed = True

    sender = ClosableSender()
    HTTPTransport("https://example.com/logs", sender=sender).close()

    assert sender.closed is True


def test_rejects_an_unknown_backend() -> None:
    with pytest.raises(ValueError, match="backend must be"):
        HTTPTransport("https://example.com/logs", backend="curl")  # type: ignore[arg-type]


def test_aiohttp_backend_without_aiohttp_gives_an_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "aiohttp", None)

    with pytest.raises(ImportError, match=r"pip install logquill\[http\]"):
        HTTPTransport("https://example.com/logs", backend="aiohttp")
