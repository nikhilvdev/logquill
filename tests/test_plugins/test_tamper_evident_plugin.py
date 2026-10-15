from __future__ import annotations

import copy

from logquill.core.logger import Logger
from logquill.plugins.tamper_evident_plugin import TamperEvidentPlugin
from logquill.transports.transport import CollectingTransport


def test_each_record_gets_a_hash_and_prev_hash() -> None:
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], plugins=[TamperEvidentPlugin()])

    record = logger.info("hello")

    assert record is not None
    assert isinstance(record["meta"]["hash"], str)
    assert record["meta"]["prev_hash"] == "0" * 64


def test_chain_links_consecutive_records() -> None:
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], plugins=[TamperEvidentPlugin()])

    first = logger.info("one")
    second = logger.info("two")

    assert first is not None and second is not None
    assert second["meta"]["prev_hash"] == first["meta"]["hash"]


def test_verify_chain_passes_on_an_untampered_log() -> None:
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], plugins=[TamperEvidentPlugin()])
    records = [logger.info(f"event {i}", n=i) for i in range(5)]

    assert TamperEvidentPlugin.verify_chain(records) is True  # type: ignore[arg-type]


def test_verify_chain_detects_an_edited_message() -> None:
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], plugins=[TamperEvidentPlugin()])
    records = [logger.info(f"event {i}", n=i) for i in range(5)]
    tampered = [copy.deepcopy(r) for r in records]
    tampered[2]["message"] = "edited after the fact"  # type: ignore[index]

    assert TamperEvidentPlugin.verify_chain(tampered) is False  # type: ignore[arg-type]


def test_verify_chain_detects_a_removed_record() -> None:
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], plugins=[TamperEvidentPlugin()])
    records = [logger.info(f"event {i}", n=i) for i in range(5)]
    tampered = records[:2] + records[3:]  # remove index 2

    assert TamperEvidentPlugin.verify_chain(tampered) is False  # type: ignore[arg-type]


def test_verify_chain_detects_reordered_records() -> None:
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], plugins=[TamperEvidentPlugin()])
    records = [logger.info(f"event {i}", n=i) for i in range(3)]
    reordered = [records[1], records[0], records[2]]

    assert TamperEvidentPlugin.verify_chain(reordered) is False  # type: ignore[arg-type]


def test_verify_chain_on_empty_input_is_true() -> None:
    assert TamperEvidentPlugin.verify_chain([]) is True


# --- head_hash / sign_head ---------------------------------------------------


def test_head_hash_starts_at_genesis_and_tracks_the_last_record() -> None:
    plugin = TamperEvidentPlugin()
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], plugins=[plugin])

    assert plugin.head_hash == "0" * 64

    record = logger.info("one")

    assert record is not None
    assert plugin.head_hash == record["meta"]["hash"]


def test_sign_head_is_deterministic_for_the_same_key_and_state() -> None:
    plugin = TamperEvidentPlugin()
    sink = CollectingTransport()
    Logger("app.test", transports=[sink], plugins=[plugin]).info("one")

    assert plugin.sign_head(b"key1") == plugin.sign_head(b"key1")
    assert plugin.sign_head(b"key1") != plugin.sign_head(b"key2")


def test_sign_head_changes_as_the_chain_grows() -> None:
    plugin = TamperEvidentPlugin()
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], plugins=[plugin])
    logger.info("one")
    sig_after_one = plugin.sign_head(b"key")
    logger.info("two")

    assert plugin.sign_head(b"key") != sig_after_one


# --- verify_chain_detailed ---------------------------------------------------


def test_verify_chain_detailed_reports_ok_with_the_head_hash() -> None:
    from logquill.plugins.tamper_evident_plugin import verify_chain_detailed

    plugin = TamperEvidentPlugin()
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], plugins=[plugin])
    logger.info("one")
    logger.info("two")

    result = verify_chain_detailed(sink.records)

    assert result.ok is True
    assert result.records_checked == 2
    assert result.broken_at is None
    assert result.head_hash == plugin.head_hash


def test_verify_chain_detailed_reports_where_an_edit_broke_the_chain() -> None:
    from logquill.plugins.tamper_evident_plugin import verify_chain_detailed

    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], plugins=[TamperEvidentPlugin()])
    logger.info("one")
    logger.info("two")
    logger.info("three")
    tampered = copy.deepcopy(sink.records)
    tampered[1]["message"] = "EDITED"

    result = verify_chain_detailed(tampered)

    assert result.ok is False
    assert result.broken_at == 2
    assert "edited after being chained" in result.reason


def test_verify_chain_detailed_reports_a_removed_line() -> None:
    from logquill.plugins.tamper_evident_plugin import verify_chain_detailed

    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], plugins=[TamperEvidentPlugin()])
    logger.info("one")
    logger.info("two")
    logger.info("three")
    records = sink.records
    with_a_line_removed = [records[0], records[2]]

    result = verify_chain_detailed(with_a_line_removed)

    assert result.ok is False
    assert result.broken_at == 2
    assert "removed" in result.reason or "edited" in result.reason


def test_verify_chain_detailed_on_an_empty_log() -> None:
    from logquill.plugins.tamper_evident_plugin import verify_chain_detailed

    result = verify_chain_detailed([])

    assert result.ok is True
    assert result.records_checked == 0
    assert result.head_hash == TamperEvidentPlugin().head_hash  # genesis hash


def test_verify_chain_still_returns_a_bare_bool_for_existing_callers() -> None:
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], plugins=[TamperEvidentPlugin()])
    logger.info("one")

    assert TamperEvidentPlugin.verify_chain(sink.records) is True


# --- verify_signed_chain / sign_head / verify_head_signature -----------------


def test_verify_signed_chain_passes_with_the_right_key_and_signature() -> None:
    from logquill.plugins.tamper_evident_plugin import verify_signed_chain

    plugin = TamperEvidentPlugin()
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], plugins=[plugin])
    logger.info("one")
    logger.info("two")
    signature = plugin.sign_head(b"shared-key")

    result = verify_signed_chain(sink.records, key=b"shared-key", signature=signature)

    assert result.ok is True


def test_verify_signed_chain_fails_on_the_wrong_key() -> None:
    from logquill.plugins.tamper_evident_plugin import verify_signed_chain

    plugin = TamperEvidentPlugin()
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], plugins=[plugin])
    logger.info("one")
    signature = plugin.sign_head(b"real-key")

    result = verify_signed_chain(sink.records, key=b"wrong-key", signature=signature)

    assert result.ok is False
    assert "signature" in result.reason


def test_verify_signed_chain_catches_a_deleted_trailing_line() -> None:
    """The exit criterion: hash-chaining alone can't catch a truncated file
    — the remaining chain is perfectly self-consistent, just shorter. A
    signature taken before the deletion catches it."""
    from logquill.plugins.tamper_evident_plugin import verify_chain_detailed, verify_signed_chain

    plugin = TamperEvidentPlugin()
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], plugins=[plugin])
    logger.info("one")
    logger.info("two")
    logger.info("three")
    signature = plugin.sign_head(b"key")  # signed once all three are in

    truncated = sink.records[:-1]  # an attacker then deletes "three" from the file

    assert verify_chain_detailed(truncated).ok is True  # internally consistent!
    result = verify_signed_chain(truncated, key=b"key", signature=signature)
    assert result.ok is False
    assert "truncated" in result.reason


def test_verify_signed_chain_passes_against_the_full_un_truncated_chain() -> None:
    from logquill.plugins.tamper_evident_plugin import verify_signed_chain

    plugin = TamperEvidentPlugin()
    sink = CollectingTransport()
    logger = Logger("app.test", transports=[sink], plugins=[plugin])
    logger.info("one")
    logger.info("two")
    logger.info("three")
    signature = plugin.sign_head(b"key")

    result = verify_signed_chain(sink.records, key=b"key", signature=signature)

    assert result.ok is True


def test_verify_head_signature_uses_constant_time_comparison() -> None:
    from logquill.plugins.tamper_evident_plugin import sign_head, verify_head_signature

    signature = sign_head("a" * 64, b"key")

    assert verify_head_signature("a" * 64, b"key", signature) is True
    assert verify_head_signature("a" * 64, b"key", "wrong") is False
    assert verify_head_signature("a" * 64, b"other-key", signature) is False
