from __future__ import annotations

import pytest

from logquill import AuditLogger
from logquill.plugins.pii_redact_plugin import PIIRedactPlugin
from logquill.plugins.redact_plugin import RedactPlugin
from logquill.plugins.tamper_evident_plugin import (
    TamperEvidentPlugin,
    verify_chain_detailed,
    verify_signed_chain,
)
from logquill.transports.transport import CollectingTransport


def _audit_logger(**kwargs: object) -> tuple[AuditLogger, CollectingTransport]:
    sink = CollectingTransport()
    return AuditLogger("audit", transports=[sink], **kwargs), sink  # type: ignore[arg-type]


def test_content_policy_defaults_to_off() -> None:
    logger, _ = _audit_logger()

    assert logger.content_policy == "off"


def test_bundles_redact_pii_and_tamper_evident_plugins() -> None:
    logger, _ = _audit_logger()

    kinds = [type(plugin) for plugin in logger.plugins]

    assert RedactPlugin in kinds
    assert PIIRedactPlugin in kinds
    assert TamperEvidentPlugin in kinds


def test_a_seeded_secret_and_pii_never_reach_the_transport() -> None:
    logger, sink = _audit_logger()

    logger.info("login", password="hunter2", note="contact a@example.com")

    assert "hunter2" not in str(sink.records[0])
    assert "a@example.com" not in str(sink.records[0])


def test_a_seeded_secret_inside_a_prompt_never_reaches_the_transport() -> None:
    logger, sink = _audit_logger()

    logger.llm_call("chat", model="m", input_messages=["the user's actual secret prompt"])

    assert "the user's actual secret prompt" not in str(sink.records[0])


def test_records_are_hash_chained() -> None:
    logger, sink = _audit_logger()

    logger.info("one")
    logger.info("two")

    assert verify_chain_detailed(sink.records).ok is True


def test_head_hash_and_sign_head_are_exposed() -> None:
    logger, sink = _audit_logger()
    logger.info("one")

    signature = logger.sign_head(b"key")

    assert logger.head_hash == sink.records[-1]["meta"]["hash"]
    result = verify_signed_chain(sink.records, key=b"key", signature=signature)
    assert result.ok is True


def test_extra_plugins_run_after_the_bundled_ones() -> None:
    from logquill.plugins.plugin import Plugin

    seen = []

    class Spy(Plugin):
        def before_log(self, record):  # type: ignore[no-untyped-def]
            seen.append(record["meta"].get("password"))  # already redacted by now
            return record

    logger, _ = _audit_logger(extra_plugins=[Spy()])

    logger.info("x", password="hunter2")

    assert seen == ["***"]


def test_content_policy_can_be_overridden() -> None:
    logger, sink = _audit_logger(content_policy="full")

    logger.llm_call("chat", model="m", input_messages=["raw prompt"])

    assert sink.records[0]["meta"]["input_messages"] == ["raw prompt"]


def test_an_invalid_content_policy_still_raises() -> None:
    with pytest.raises(ValueError, match="content_policy"):
        AuditLogger("audit", content_policy="plaintext")


def test_child_continues_the_same_hash_chain_as_the_parent() -> None:
    logger, sink = _audit_logger()
    child = logger.child("sub")

    logger.info("from parent")
    child.info("from child")

    assert isinstance(child, AuditLogger)
    assert verify_chain_detailed(sink.records).ok is True
    assert sink.records[1]["meta"]["prev_hash"] == sink.records[0]["meta"]["hash"]


def test_child_also_redacts_secrets_and_pii() -> None:
    logger, sink = _audit_logger()
    child = logger.child("sub")

    child.info("x", password="hunter2")

    assert "hunter2" not in str(sink.records[-1])


def test_child_inherits_content_policy() -> None:
    logger, _ = _audit_logger(content_policy="hash")

    child = logger.child("sub")

    assert child.content_policy == "hash"


def test_kwargs_are_forwarded_to_logger_init() -> None:
    logger, _ = _audit_logger(flush_at_exit=False)

    from logquill.runtime import shutdown

    assert logger not in shutdown._loggers
