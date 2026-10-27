from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from logquill import _native
from logquill.core.records import LogRecord
from logquill.plugins.plugin import Plugin

GENESIS_HASH = _native.GENESIS_HASH


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
    """Like `TamperEvidentPlugin.verify_chain`, but reports where and why, instead of a bare
    `bool` — what `logquill verify` uses to print an actionable result. `records_checked` counts
    every record looked at, including the one the chain broke on; `head_hash` is the chain's hash as
    of the last record verified (the full chain's head, on success). Checked in Rust, with the same
    result as the `logquill verify` command.
    """
    return VerificationResult(**_native.verify_chain(records, genesis_hash))


def sign_head(head_hash: str, key: bytes) -> str:
    """HMAC-SHA256 of `head_hash` under `key`, hex-encoded — call this with
    `TamperEvidentPlugin.head_hash` (or `.sign_head(key)`, which does exactly this) at any point you
    want to be able to prove the chain's state later, e.g. right before `logger.close()`.

    **Store the result somewhere the log file itself doesn't live** — a secrets vault, a separate
    append-only ledger, a console the tamperer doesn't control. A signature stored inside the same
    file it attests to protects nothing: whoever can edit the log can edit the signature next to it
    too. What this buys you: internal hash-chaining alone can't detect the file being truncated and
    the last N lines dropped (the remaining chain is perfectly self-consistent, just shorter);
    comparing the chain's current head against an independently-held signature can.
    """
    return _native.sign_head(head_hash, key)  # type: ignore[no-any-return]


def verify_head_signature(head_hash: str, key: bytes, signature: str) -> bool:
    """Whether `signature` is what `sign_head(head_hash, key)` would produce — compared in constant
    time, so this doesn't leak how much of `signature` matched."""
    return _native.verify_head_signature(head_hash, key, signature)  # type: ignore[no-any-return]


def verify_signed_chain(
    records: Iterable[Mapping[str, Any]],
    *,
    key: bytes,
    signature: str,
    genesis_hash: str = GENESIS_HASH,
) -> VerificationResult:
    """`verify_chain_detailed`, plus confirming the chain's resulting head matches an
    independently-held `signature` — see `sign_head` for why that catches what hash-chaining alone
    can't: the file being truncated (or wholesale replaced) after the signature was taken.
    """
    return VerificationResult(**_native.verify_signed_chain(records, key, signature, genesis_hash))


class TamperEvidentPlugin(Plugin):
    """Hash-chains every record so tampering with a written log can be detected after the fact.

    Each record gets `meta.hash` = a SHA-256 hex digest over the record's own content plus the
    previous record's hash (`meta.prev_hash`) — the same hash-chain construction used by
    tamper-evident/append-only logs: editing or deleting any one line breaks every hash after it in
    the chain, even if the tamperer edits the file directly and not through this plugin. Opt-in —
    hashing every record has a real, measurable CPU cost, so it isn't part of the default pipeline.

    The chain is made **when a record is written**, one record at a time, in the order the
    transports receive them (decision D22), so it stays valid however many threads log. That means
    the record a log call returns does not yet carry `meta.hash`; the written record, and the one
    an `after_log` hook sees, does.

    Verify a previously-written log with `TamperEvidentPlugin.verify_chain`, which re-derives each
    record's hash from its content and confirms it matches both the stored `meta.hash` and the
    chain built from the records before it, in order (`logquill verify <file>` wraps this).
    `verify_chain_detailed` reports *where* and *why* a chain broke instead of a bare `bool`.

    Hash-chaining alone can't catch a file being truncated — a tamperer who deletes the last N lines
    leaves a perfectly self-consistent, just shorter, chain. `sign_head(key)` (or the module-level
    `sign_head`) signs the chain's current head so that can be caught too: see `verify_signed_chain`
    and `sign_head`'s own docstring for where to keep that signature.
    """

    def __init__(self, *, genesis_hash: str = GENESIS_HASH) -> None:
        """`genesis_hash` is the `prev_hash` used for the very first record in the chain; override it
        to start a new chain that continues from a previously-recorded hash (e.g. across a log
        rotation)."""
        self._genesis_hash = genesis_hash
        self._chain = _native.Chain(genesis_hash)

    def before_log(self, record: LogRecord) -> LogRecord | None:
        """Does nothing: the dispatcher chains the record when it writes it (decision D22)."""
        return record

    @property
    def head_hash(self) -> str:
        """This instance's current chain head — the `hash` of the last record written through it,
        or `genesis_hash` if none has been. Pass to `sign_head`/`.sign_head()` to make the chain's
        current state provable later."""
        return self._chain.head()  # type: ignore[no-any-return, attr-defined]

    def sign_head(self, key: bytes) -> str:
        """`sign_head(self.head_hash, key)` — see the module-level `sign_head` for what to do with
        the result and why."""
        return self._chain.sign_head(key)  # type: ignore[no-any-return, attr-defined]

    @staticmethod
    def verify_chain(
        records: Iterable[Mapping[str, Any]], *, genesis_hash: str = GENESIS_HASH
    ) -> bool:
        """Return `True` iff every record's hash matches its content plus the previous record's
        hash, in the given order. Returns `False` at the first break in the chain (an edited,
        removed, or reordered record). See `verify_chain_detailed` for *where* and *why* it broke.
        """
        return verify_chain_detailed(records, genesis_hash=genesis_hash).ok
