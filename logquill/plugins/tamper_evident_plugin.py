from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from logquill.core.records import LEGACY_SCHEMA_VERSION, LogRecord
from logquill.plugins.plugin import Plugin

GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class VerificationResult:
    """What `verify_chain_detailed`/`verify_signed_chain` found.

    `ok=False` means stop reading `broken_at` as "trustworthy up to
    here" — it's exactly where corruption was *detected*, not necessarily
    where it was *introduced* (a deleted line, for instance, is detected at
    the record right after the gap).
    """

    ok: bool
    records_checked: int
    broken_at: int | None
    reason: str | None
    head_hash: str | None


def verify_chain_detailed(
    records: Iterable[Mapping[str, Any]], *, genesis_hash: str = GENESIS_HASH
) -> VerificationResult:
    """Like `TamperEvidentPlugin.verify_chain`, but reports where and why,
    instead of a bare `bool` — what `logquill verify` uses to print an
    actionable result. `records_checked` counts every record looked at,
    including the one the chain broke on; `head_hash` is the chain's hash
    as of the last record verified (the full chain's head, on success).
    """
    prev_hash = genesis_hash
    checked = 0
    for record in records:
        checked += 1
        meta = record.get("meta", {})
        stored_hash = meta.get("hash")
        stored_prev_hash = meta.get("prev_hash")
        if stored_hash is None:
            return VerificationResult(
                False,
                checked,
                checked,
                "this record has no meta.hash — it was never chained, or that field was removed",
                prev_hash if checked > 1 else None,
            )
        if stored_prev_hash != prev_hash:
            return VerificationResult(
                False,
                checked,
                checked,
                "meta.prev_hash doesn't match the previous record's hash — a line before "
                "this one was edited, removed, or reordered",
                prev_hash,
            )
        if _compute_hash(record, prev_hash) != stored_hash:
            return VerificationResult(
                False,
                checked,
                checked,
                "this record's content doesn't match its own stored hash — this line was "
                "edited after being chained",
                prev_hash,
            )
        prev_hash = stored_hash
    return VerificationResult(True, checked, None, None, prev_hash if checked else genesis_hash)


def sign_head(head_hash: str, key: bytes) -> str:
    """HMAC-SHA256 of `head_hash` under `key`, hex-encoded — call this with
    `TamperEvidentPlugin.head_hash` (or `.sign_head(key)`, which does exactly
    this) at any point you want to be able to prove the chain's state later,
    e.g. right before `logger.close()`.

    **Store the result somewhere the log file itself doesn't live** — a
    secrets vault, a separate append-only ledger, a console the tamperer
    doesn't control. A signature stored inside the same file it attests to
    protects nothing: whoever can edit the log can edit the signature next
    to it too. What this buys you: internal hash-chaining alone can't
    detect the file being truncated and the last N lines dropped (the
    remaining chain is perfectly self-consistent, just shorter) — comparing
    the chain's current head against an independently-held signature can.
    """
    return hmac.new(key, head_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_head_signature(head_hash: str, key: bytes, signature: str) -> bool:
    """Whether `signature` is what `sign_head(head_hash, key)` would
    produce — using `hmac.compare_digest`, so this doesn't leak timing
    information about how much of `signature` matched."""
    return hmac.compare_digest(sign_head(head_hash, key), signature)


def verify_signed_chain(
    records: Iterable[Mapping[str, Any]],
    *,
    key: bytes,
    signature: str,
    genesis_hash: str = GENESIS_HASH,
) -> VerificationResult:
    """`verify_chain_detailed`, plus confirming the chain's resulting head
    matches an independently-held `signature` — see `sign_head` for why
    that catches what hash-chaining alone can't: the file being truncated
    (or wholesale replaced) after the signature was taken.
    """
    result = verify_chain_detailed(records, genesis_hash=genesis_hash)
    if not result.ok:
        return result
    if result.head_hash is None or not verify_head_signature(result.head_hash, key, signature):
        return VerificationResult(
            False,
            result.records_checked,
            result.records_checked,
            "the chain is internally consistent, but its head doesn't match the given "
            "signature — the file may have been truncated, or replaced outright, after "
            "the signature was taken",
            result.head_hash,
        )
    return result


class TamperEvidentPlugin(Plugin):
    """Hash-chains every record so tampering with a written log can be
    detected after the fact.

    Each record gets `meta.hash` = a SHA-256 hex digest over the record's
    own content plus the previous record's hash (`meta.prev_hash`) — the
    same hash-chain construction used by tamper-evident/append-only logs:
    editing or deleting any one line breaks every hash after it in the
    chain, even if the tamperer edits the file directly and not through
    this plugin. Opt-in — hashing every record has a real, measurable CPU
    cost, so it isn't part of the default pipeline.

    Verify a previously-written log with `TamperEvidentPlugin.verify_chain`,
    which re-derives each record's hash from its content and confirms it
    matches both the stored `meta.hash` and the chain built from the
    records before it, in order (`logquill verify <file>` wraps this).
    `verify_chain_detailed` reports *where* and *why* a chain broke instead
    of a bare `bool`.

    Hash-chaining alone can't catch a file being truncated — a tamperer who
    deletes the last N lines leaves a perfectly self-consistent, just
    shorter, chain. `sign_head(key)` (or the module-level `sign_head`)
    signs the chain's current head so that can be caught too: see
    `verify_signed_chain` and `sign_head`'s own docstring for where to keep
    that signature.
    """

    def __init__(self, *, genesis_hash: str = GENESIS_HASH) -> None:
        """`genesis_hash` is the `prev_hash` used for the very first record
        in the chain; override it to start a new chain that continues from
        a previously-recorded hash (e.g. across a log rotation)."""
        self._genesis_hash = genesis_hash
        self._last_hash = genesis_hash

    def before_log(self, record: LogRecord) -> LogRecord | None:
        """Stamps `meta.prev_hash`/`meta.hash`, chaining this record onto
        the previous one this plugin instance processed."""
        prev_hash = self._last_hash
        digest = _compute_hash(record, prev_hash)
        record["meta"] = {**record["meta"], "prev_hash": prev_hash, "hash": digest}
        self._last_hash = digest
        return record

    @property
    def head_hash(self) -> str:
        """This instance's current chain head — the `hash` of the last
        record it processed, or `genesis_hash` if it hasn't processed one
        yet. Pass to `sign_head`/`.sign_head()` to make the chain's current
        state provable later."""
        return self._last_hash

    def sign_head(self, key: bytes) -> str:
        """`sign_head(self.head_hash, key)` — see the module-level
        `sign_head` for what to do with the result and why."""
        return sign_head(self._last_hash, key)

    @staticmethod
    def verify_chain(
        records: Iterable[Mapping[str, Any]], *, genesis_hash: str = GENESIS_HASH
    ) -> bool:
        """Return `True` iff every record's hash matches its content plus the
        previous record's hash, in the given order. Returns `False` at the
        first break in the chain (an edited, removed, or reordered record).
        See `verify_chain_detailed` for *where* and *why* it broke.
        """
        return verify_chain_detailed(records, genesis_hash=genesis_hash).ok


def _compute_hash(record: Mapping[str, Any], prev_hash: str) -> str:
    meta = record.get("meta", {})
    content: dict[str, Any] = {
        "timestamp": record.get("timestamp"),
        "level": record.get("level"),
        "logger": record.get("logger"),
        "message": record.get("message"),
        "meta": {k: v for k, v in meta.items() if k not in ("hash", "prev_hash")},
    }
    # `schema_version` and `llm` are covered when present, so editing either is
    # caught. A `schema_version` of "1.0" is what `parse_record` labels a record
    # that had none, so it's left out: a chain written by logquill 1.x verifies
    # the same before and after parsing.
    if record.get("schema_version", LEGACY_SCHEMA_VERSION) != LEGACY_SCHEMA_VERSION:
        content["schema_version"] = record["schema_version"]
    if "llm" in record:
        content["llm"] = record["llm"]
    payload = json.dumps(content, sort_keys=True, default=str)
    return hashlib.sha256(f"{prev_hash}{payload}".encode()).hexdigest()
