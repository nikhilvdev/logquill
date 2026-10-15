from __future__ import annotations

from logquill.plugins.statsd_metrics_backend import StatsDBackend


class FakeSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False
        self._fail = fail

    def sendto(self, data: bytes, address: tuple[str, int]) -> int:
        if self._fail:
            raise OSError("network unreachable")
        self.sent.append((data, address))
        return len(data)

    def close(self) -> None:
        self.closed = True


def test_increment_sends_a_counter_line() -> None:
    sock = FakeSocket()
    backend = StatsDBackend(sock=sock)

    backend.increment("logquill.errors", tags={})

    (data, address) = sock.sent[0]
    assert data == b"logquill.errors:1|c"
    assert address == ("localhost", 8125)


def test_observe_sends_a_histogram_line() -> None:
    sock = FakeSocket()
    backend = StatsDBackend(sock=sock)

    backend.observe("logquill.duration_ms", 12.5, tags={})

    (data, _address) = sock.sent[0]
    assert data == b"logquill.duration_ms:12.5|h"


def test_tags_are_rendered_as_a_dogstatsd_suffix() -> None:
    sock = FakeSocket()
    backend = StatsDBackend(sock=sock)

    backend.increment("logquill.errors", tags={"logger": "app.agent"})

    (data, _address) = sock.sent[0]
    assert data == b"logquill.errors:1|c|#logger:app.agent"


def test_prefix_is_prepended_to_the_metric_name() -> None:
    sock = FakeSocket()
    backend = StatsDBackend(sock=sock, prefix="myapp")

    backend.increment("errors", tags={})

    (data, _address) = sock.sent[0]
    assert data == b"myapp.errors:1|c"


def test_host_and_port_are_forwarded_to_the_socket_address() -> None:
    sock = FakeSocket()
    backend = StatsDBackend(sock=sock, host="metrics.internal", port=9125)

    backend.increment("errors", tags={})

    (_data, address) = sock.sent[0]
    assert address == ("metrics.internal", 9125)


def test_a_failed_send_is_swallowed_not_raised() -> None:
    sock = FakeSocket(fail=True)
    backend = StatsDBackend(sock=sock)

    backend.increment("errors", tags={})  # must not raise


def test_close_closes_the_underlying_socket() -> None:
    sock = FakeSocket()
    backend = StatsDBackend(sock=sock)

    backend.close()

    assert sock.closed is True
