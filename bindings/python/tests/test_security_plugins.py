"""Redaction, PII scanning and the tamper chain, which now run in the Rust core."""

import json
import threading

import pytest

import logquill
from logquill import (
    AuditLogger,
    CollectingTransport,
    FileTransport,
    Logger,
    PIIRedactPlugin,
    Plugin,
    RedactPlugin,
    TamperEvidentPlugin,
    verify_chain_detailed,
    verify_signed_chain,
)


def written(plugin, **meta):
    sink = CollectingTransport()
    log = Logger("app", transports=[sink], plugins=[plugin], async_dispatch=False)
    log.info("m", **meta)
    return sink.records[0]["meta"] if sink.records else None


# ---- RedactPlugin ---------------------------------------------------------------------------


def test_redact_matches_keys_case_insensitively_at_every_depth():
    meta = written(
        RedactPlugin(),
        Password="p",
        nested={"token": "t", "ok": 1, "deeper": [{"API_KEY": "k"}, "plain"]},
        keep="yes",
    )
    assert meta == {
        "Password": "***",
        "nested": {"token": "***", "ok": 1, "deeper": [{"API_KEY": "***"}, "plain"]},
        "keep": "yes",
    }


def test_redact_takes_custom_keys_a_replacement_and_field_classes():
    assert written(RedactPlugin(keys=["ssn"], replacement="<hidden>"), ssn="1", password="p") == {
        "ssn": "<hidden>",
        "password": "p",
    }
    meta = written(RedactPlugin(keys=(), classes=["content"]), input_messages=[1], other=2)
    assert meta["input_messages"] == "***" and meta["other"] == 2


def test_redact_survives_a_self_referencing_meta():
    loop = {"password": "p"}
    loop["self"] = loop
    meta = written(RedactPlugin(), loop=loop)
    assert meta["loop"]["password"] == "***"


def test_redact_hides_locals_in_a_diagnose_traceback():
    sink = CollectingTransport()
    log = Logger("app", transports=[sink], plugins=[RedactPlugin()], async_dispatch=False)
    password = "hunter2-do-not-leak"  # noqa: F841 — read by the traceback formatter
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log.error("failed", diagnose=True)
    stack = sink.records[0]["meta"]["stack"]
    assert "hunter2-do-not-leak" not in stack


# ---- fail closed (D20) ----------------------------------------------------------------------


class BrokenRedactor(RedactPlugin):
    def before_log(self, record):
        raise RuntimeError("redactor crashed")


class BrokenNeutral(Plugin):
    def before_log(self, record):
        raise RuntimeError("neutral crashed")


def test_a_redactor_that_fails_drops_the_record_instead_of_passing_it_through(caplog):
    sink = CollectingTransport()
    log = Logger("app", transports=[sink], plugins=[BrokenRedactor()], async_dispatch=False)
    assert log.info("secret", password="p") is None
    assert sink.records == []
    assert "withheld" in caplog.text


def test_an_ordinary_plugin_that_fails_is_skipped_and_the_record_goes_on():
    sink = CollectingTransport()
    log = Logger("app", transports=[sink], plugins=[BrokenNeutral()], async_dispatch=False)
    assert log.info("kept") is not None
    assert len(sink.records) == 1


def test_a_meta_that_cannot_be_redacted_drops_the_record():
    sink = CollectingTransport()
    log = Logger("app", transports=[sink], plugins=[RedactPlugin()], async_dispatch=False)
    assert log.info("m", weird={(1, 2): "tuple key"}) is None
    assert sink.records == []


# ---- PIIRedactPlugin ------------------------------------------------------------------------


def test_pii_scans_every_string_at_every_depth_with_the_twelve_default_patterns():
    secret_key = "sk-" + "a" * 30
    meta = written(
        PIIRedactPlugin(),
        notes="mail bob@example.com or call 415-555-1234, ssn 123-45-6789",
        nested={"list": ["x", f"key {secret_key}", {"deep": "carol@example.org"}]},
        number=42,
        flag=True,
    )
    assert meta["notes"] == "mail *** or call ***, ssn ***"
    assert meta["nested"]["list"] == ["x", "key ***", {"deep": "***"}]
    assert meta["number"] == 42 and meta["flag"] is True


def test_pii_scans_the_text_of_objects_that_are_not_json():
    class Contact:
        def __str__(self):
            return "reach dave@example.com"

    assert written(PIIRedactPlugin(), who=Contact())["who"] == "reach ***"


def test_pii_with_custom_patterns_uses_the_python_path():
    import re

    plugin = PIIRedactPlugin(patterns={"order": re.compile(r"ORD-\d+")}, replacement="[x]")
    assert plugin._native is None
    assert written(plugin, text="see ORD-123 and bob@example.com")["text"] == "see [x] and bob@example.com"


def test_pii_redact_local_scrubs_a_captured_variable():
    assert PIIRedactPlugin().redact_local("who", "'bob@example.com'") == "'***'"


# ---- the tamper chain -----------------------------------------------------------------------


def read_log(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_a_chained_file_verifies_and_every_kind_of_tampering_is_caught(tmp_path):
    path = tmp_path / "audit.log"
    plugin = TamperEvidentPlugin()
    log = Logger("app", transports=[FileTransport(path)], plugins=[plugin])
    for i in range(6):
        log.info("m", i=i, note="café ☃")
    log.close()
    records = read_log(path)
    assert records[0]["meta"]["prev_hash"] == logquill.plugins.tamper_evident_plugin.GENESIS_HASH
    assert [r["meta"]["prev_hash"] for r in records[1:]] == [r["meta"]["hash"] for r in records[:-1]]
    assert verify_chain_detailed(records).ok
    assert TamperEvidentPlugin.verify_chain(records) is True
    assert plugin.head_hash == records[-1]["meta"]["hash"]

    edited = json.loads(json.dumps(records))
    edited[3]["meta"]["i"] = 99
    result = verify_chain_detailed(edited)
    assert not result.ok and result.broken_at == 4 and "edited after being chained" in result.reason
    removed = records[:2] + records[3:]
    assert verify_chain_detailed(removed).broken_at == 3
    assert "no meta.hash" in verify_chain_detailed([{"meta": {}}]).reason


def test_truncation_is_caught_only_with_a_signed_head(tmp_path):
    path = tmp_path / "audit.log"
    plugin = TamperEvidentPlugin()
    log = Logger("app", transports=[FileTransport(path)], plugins=[plugin])
    for i in range(5):
        log.info("m", i=i)
    log.close()
    key = b"k" * 32
    signature = plugin.sign_head(key)
    records = read_log(path)
    assert verify_signed_chain(records, key=key, signature=signature).ok
    shorter = verify_signed_chain(records[:3], key=key, signature=signature)
    assert not shorter.ok and "truncated" in shorter.reason
    assert not verify_signed_chain(records, key=b"wrong", signature=signature).ok
    assert logquill.verify_head_signature(plugin.head_hash, key, signature)


@pytest.mark.parametrize("async_dispatch", [True, False])
def test_the_chain_stays_valid_when_many_threads_log_at_once(tmp_path, async_dispatch):
    path = tmp_path / "busy.log"
    plugin = TamperEvidentPlugin()
    log = Logger("app", transports=[FileTransport(path)], plugins=[plugin], async_dispatch=async_dispatch)

    def spam(t):
        for i in range(250):
            log.info("m", t=t, i=i)

    threads = [threading.Thread(target=spam, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    log.close()
    records = read_log(path)
    assert len(records) == 2000
    result = verify_chain_detailed(records)
    assert result.ok, result.reason
    assert result.head_hash == plugin.head_hash


def test_after_log_sees_the_record_as_written_with_its_hash():
    seen = []

    class Watch(Plugin):
        def after_log(self, record):
            seen.append(record["meta"].get("hash"))

    sink = CollectingTransport()
    log = Logger("app", transports=[sink], plugins=[TamperEvidentPlugin(), Watch()])
    log.info("m")
    log.close()
    assert seen == [sink.records[0]["meta"]["hash"]] and seen[0]


def test_a_logger_without_transports_still_chains_what_it_returns():
    log = Logger("app", plugins=[TamperEvidentPlugin()])
    first, second = log.info("a"), log.info("b")
    assert second["meta"]["prev_hash"] == first["meta"]["hash"]


def test_an_audit_logger_and_its_children_write_one_chain(tmp_path):
    path = tmp_path / "audit.log"
    audit = AuditLogger("audit", transports=[FileTransport(path)])
    child = audit.child("payments", tenant="t1")
    audit.info("one", password="p")
    child.info("two", email="bob@example.com")
    audit.info("three")
    audit.close()
    records = read_log(path)
    assert [r["message"] for r in records] == ["one", "two", "three"]
    assert verify_chain_detailed(records).ok
    assert records[0]["meta"]["password"] == "***" and records[1]["meta"]["email"] == "***"
    assert records[1]["meta"]["tenant"] == "t1"
    assert audit.head_hash == records[-1]["meta"]["hash"]
