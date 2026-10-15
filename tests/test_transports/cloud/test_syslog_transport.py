from __future__ import annotations

import pytest

from logquill.core.logger import Logger
from logquill.transports.cloud.syslog_transport import SyslogTransport


class FakeSender:
    def __init__(self) -> None:
        self.calls: list[bytes] = []

    def __call__(self, data: bytes) -> None:
        self.calls.append(data)


def test_pri_reflects_facility_and_severity() -> None:
    sender = FakeSender()
    transport = SyslogTransport(facility=1, sender=sender)
    logger = Logger("app.test", transports=[transport])

    logger.error("boom")

    assert len(sender.calls) == 1
    message = sender.calls[0].decode("utf-8")
    # facility 1 * 8 + severity 3 (error) = 11
    assert message.startswith("<11>1 ")


def test_default_facility_is_user() -> None:
    sender = FakeSender()
    transport = SyslogTransport(sender=sender)
    logger = Logger("app.test", transports=[transport], level="trace")

    logger.info("hi")  # facility 1 * 8 + severity 6 = 14

    message = sender.calls[0].decode("utf-8")
    assert message.startswith("<14>1 ")


def test_app_name_defaults_to_logger_name() -> None:
    sender = FakeSender()
    transport = SyslogTransport(sender=sender)
    logger = Logger("billing.service", transports=[transport])

    logger.info("charged")

    message = sender.calls[0].decode("utf-8")
    parts = message.split(" ")
    # <PRI>VERSION TIMESTAMP HOSTNAME APP-NAME PROCID MSGID SD MSG...
    assert parts[3] == "billing.service"


def test_explicit_app_name_overrides_logger_name() -> None:
    sender = FakeSender()
    transport = SyslogTransport(sender=sender, app_name="custom-app")
    logger = Logger("billing.service", transports=[transport])

    logger.info("charged")

    message = sender.calls[0].decode("utf-8")
    parts = message.split(" ")
    assert parts[3] == "custom-app"


def test_message_body_carries_the_formatted_record() -> None:
    sender = FakeSender()
    transport = SyslogTransport(sender=sender)
    logger = Logger("app.test", transports=[transport])

    logger.info("hello world", user_id=42)

    message = sender.calls[0].decode("utf-8")
    assert '"message":"hello world"' in message
    assert '"user_id":42' in message


def test_udp_messages_have_no_trailing_newline() -> None:
    sender = FakeSender()
    transport = SyslogTransport(sender=sender, protocol="udp")
    logger = Logger("app.test", transports=[transport])

    logger.info("hi")

    assert not sender.calls[0].endswith(b"\n")


def test_tcp_messages_are_newline_framed() -> None:
    sender = FakeSender()
    transport = SyslogTransport(sender=sender, protocol="tcp")
    logger = Logger("app.test", transports=[transport])

    logger.info("hi")

    assert sender.calls[0].endswith(b"\n")


def test_invalid_protocol_raises() -> None:
    with pytest.raises(ValueError, match="protocol must be 'udp' or 'tcp'"):
        SyslogTransport(protocol="quic")


def test_close_closes_the_real_socket_when_one_was_opened() -> None:
    # No injected sender — forces the real (UDP) socket path, closed against
    # an address that just needs to be well-formed, never actually reached.
    transport = SyslogTransport(host="127.0.0.1", port=1)
    logger = Logger("app.test", transports=[transport])

    logger.info("hi")  # opens the real socket via `_resolved_sender`

    assert transport._sock is not None
    transport.close()
    assert transport._sock is None
