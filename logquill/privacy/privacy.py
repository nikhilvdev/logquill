"""Field classification and the content-capture policy: the two pieces of
LogQuill's privacy controls that aren't specific to one plugin.

**Field classification** lets a redaction rule say "every `secret`-classed
field" instead of listing key names one by one — `RedactPlugin(classes=
["secret"])` redacts everything in `FIELD_CLASSES` tagged `"secret"`, which
is also what its own default (no arguments) now resolves to, so nothing
about `RedactPlugin`'s existing behavior changed by adding this.

**Content capture** governs the content fields specifically — an LLM call's
prompt/completion (`meta.input_messages`/`output_messages`), a tool call's
arguments/result (`meta.tool_arguments`/`tool_result`), a span's captured
state (`meta.state_diff`), and a model's system prompt
(`meta.system_instructions`). Unlike a `secret` (always redact) or `pii`
(scan for a pattern), this content is sometimes exactly what you want to
see while debugging and sometimes exactly what you must never write to a
transport — so it's governed by an explicit, per-logger policy
(`Logger(content_policy=...)`), **off by default**, applied unconditionally
inside `Logger._log()` for every record regardless of call site (span
closes, the stdlib bridge, a framework adapter) rather than being a plugin
someone has to remember to attach.

Same scope-honesty note as the rest of the plugin pipeline: none of this
makes a deployment "compliant" with anything — it's a technical control
that commonly supports compliance work, not a guarantee of it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Literal

_logger = logging.getLogger("logquill")

FieldClass = Literal["secret", "pii", "content"]

#: Which class a well-known `meta` key belongs to. `secret`: always-redact
#: credentials. `pii`: personal data `PIIRedactPlugin` also scans for by
#: pattern regardless of key. `content`: prompt/completion/tool-call data
#: governed by `ContentCapturePolicy` instead of a flat redact-or-don't rule.
FIELD_CLASSES: dict[str, FieldClass] = {
    "password": "secret",
    "token": "secret",
    "secret": "secret",
    "api_key": "secret",
    "authorization": "secret",
    "email": "pii",
    "ssn": "pii",
    "phone": "pii",
    "input_messages": "content",
    "output_messages": "content",
    "system_instructions": "content",
    "tool_arguments": "content",
    "tool_result": "content",
    "state_diff": "content",
}

#: The `meta` keys `Logger(content_policy=...)` governs — see the module
#: docstring. Kept as its own tuple (rather than deriving it from
#: `FIELD_CLASSES` on every call) since `Logger._log` checks it on every
#: record.
CONTENT_FIELDS: tuple[str, ...] = tuple(
    key for key, cls in FIELD_CLASSES.items() if cls == "content"
)


def keys_in_class(
    field_class: FieldClass, registry: dict[str, FieldClass] = FIELD_CLASSES
) -> set[str]:
    """Every key `registry` classifies as `field_class`."""
    return {key for key, cls in registry.items() if cls == field_class}


ContentCapturePolicy = Literal["off", "hash", "truncate", "full"]
_VALID_POLICIES = ("off", "hash", "truncate", "full")

#: How much of a `truncate`-policy value's JSON text survives.
TRUNCATE_CHARS = 200

_OFF_PLACEHOLDER = "<content capture is off — see Logger(content_policy=...)>"


def parse_content_policy(policy: ContentCapturePolicy | str) -> ContentCapturePolicy:
    """Validates `policy`, raising `ValueError` naming the valid choices if
    it isn't one of them. Accepts a plain string so it can come from config
    the same way a `Level` does."""
    if policy not in _VALID_POLICIES:
        raise ValueError(f"content_policy must be one of {_VALID_POLICIES}, got {policy!r}")
    return policy  # type: ignore[return-value]


def _to_text(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError, RecursionError):
        return str(value)


def _apply_one(value: Any, policy: ContentCapturePolicy) -> Any:
    if policy == "full":
        return value
    if policy == "off":
        return _OFF_PLACEHOLDER
    text = _to_text(value)
    if policy == "hash":
        digest = hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()
        return f"sha256:{digest}"
    # truncate
    if len(text) <= TRUNCATE_CHARS:
        return text
    return text[:TRUNCATE_CHARS] + f"...(truncated, {len(text)} chars total)"


def apply_content_policy(
    meta: dict[str, Any],
    policy: ContentCapturePolicy,
    *,
    fields: tuple[str, ...] = CONTENT_FIELDS,
) -> None:
    """Applies `policy` to every key in `fields` that's present in `meta`,
    in place. `"full"` is a no-op (by far the common case — most records
    carry no content field at all — so this returns immediately rather than
    walking `fields` against an empty intersection).

    - `"off"` (default): the value is replaced with a fixed placeholder
      string. Never the raw content, and never silently dropped either —
      a reader can tell capture was configured off rather than wondering
      why the field is simply missing.
    - `"hash"`: replaced with `"sha256:<digest>"` of the value's JSON text.
      The same content always hashes the same, so two records can be
      confirmed to share a prompt/completion without either ever being
      written anywhere.
    - `"truncate"`: the value's JSON text, cut to `TRUNCATE_CHARS`
      characters with a marker noting the original length.
    - `"full"`: passed through unchanged.

    A value that can't be JSON-serialized (a circular reference, a raw
    object) falls back to `str()` for `"hash"`/`"truncate"` rather than
    raising — a log call must not fail over this.
    """
    if policy == "full":
        return
    for field in fields:
        if field in meta:
            meta[field] = _apply_one(meta[field], policy)
