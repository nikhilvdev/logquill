from __future__ import annotations

import copy
import logging
import time
import uuid
from contextvars import ContextVar, Token
from types import TracebackType
from typing import TYPE_CHECKING, Any, Callable

from logquill.core.levels import Level

if TYPE_CHECKING:
    from logquill.core.logger import Logger

_logger = logging.getLogger("logquill")

_current_span_id: ContextVar[str | None] = ContextVar("logquill_span_id", default=None)


def current_span_id() -> str | None:
    """The `span_id` of the innermost `Logger.span()` block active in this
    execution context (thread or asyncio task), or `None` outside any span.
    """
    return _current_span_id.get()


_UNCAPTURED = object()


def _snapshot(capture: Callable[[], Any]) -> Any:
    """A deep copy of the captured state — a copy, because the state usually
    mutates in place and a reference would show "after" for both. Returns
    `_UNCAPTURED` if the callable raises or the value can't be copied, so a
    broken snapshot never breaks the code being traced."""
    try:
        return copy.deepcopy(capture())
    except Exception:
        _logger.debug("span: capture_state failed; recording no state_diff", exc_info=True)
        return _UNCAPTURED


def _state_diff(before: Any, after: Any) -> dict[str, Any] | None:
    """What changed between two snapshots, or `None` if nothing did. For two
    dicts, only the keys that differ (a key added shows only under `after`, one
    removed only under `before`); otherwise the two whole values."""
    try:
        if isinstance(before, dict) and isinstance(after, dict):
            changed = [
                key
                for key in before.keys() | after.keys()
                if key not in before or key not in after or before[key] != after[key]
            ]
            if not changed:
                return None
            return {
                "before": {key: before[key] for key in changed if key in before},
                "after": {key: after[key] for key in changed if key in after},
            }
        return None if before == after else {"before": before, "after": after}
    except Exception:
        return None


def new_span_id() -> str:
    """A 16-hex-char id, matching the shape of an OTel span id."""
    return uuid.uuid4().hex[:16]


class SpanContext:
    """Context manager returned by `Logger.span()`.

    On exit, emits one record for the span itself carrying `meta.span_id`,
    `meta.duration_ms`, and — if nested inside another span — `meta.
    parent_span_id`. Every record logged *inside* the block, through any
    `Logger` method and not just this one, is automatically stamped with
    `meta.parent_span_id` pointing at this span: `Logger._log` checks
    `current_span_id()` on every call, so nesting falls out of ordinary
    `with`-block scoping rather than anything `SpanContext` tracks itself.

    Backed by a `contextvars.ContextVar` rather than a plain attribute so
    concurrent asyncio tasks sharing one `Logger` don't see each other's
    span nesting.

    `span_id`/`parent_span_id` are normally left to auto-generate/auto-nest,
    but can be given explicitly — e.g. by a framework adapter translating an
    id it already received (LangChain's `run_id`/`parent_run_id`) directly
    onto this span, rather than minting a new, unrelated id.
    """

    def __init__(
        self,
        logger: Logger,
        name: str,
        /,
        *,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        capture_state: Callable[[], Any] | None = None,
        **meta: Any,
    ) -> None:
        """`capture_state`, if given, is called on entering and on leaving the
        block; what changed between the two lands in `meta.state_diff` (see
        `Logger.span`). `span_id` defaults to a freshly generated id; `parent_span_id`
        overrides the auto-nesting that would otherwise come from any
        enclosing span active in this execution context — see the class
        docstring for why a caller would pass either explicitly."""
        self._logger = logger
        self._name = name
        self._meta = meta
        self._span_id = span_id or new_span_id()
        self._explicit_parent_span_id = parent_span_id
        self._capture_state = capture_state
        self._state_before: Any = _UNCAPTURED
        self._token: Token[str | None] | None = None
        self._start = 0.0

    def __enter__(self) -> SpanContext:
        """Push this span's id as the current span for this execution
        context and start its duration timer."""
        if self._capture_state is not None:
            self._state_before = _snapshot(self._capture_state)
        self._token = _current_span_id.set(self._span_id)
        self._start = time.monotonic()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Pop this span off the current execution context and emit its
        record — at `ERROR` with `meta.error` set if the block raised,
        `INFO` otherwise. Does not suppress the exception; it still
        propagates after this returns."""
        duration_ms = (time.monotonic() - self._start) * 1000
        assert self._token is not None
        _current_span_id.reset(self._token)

        meta: dict[str, Any] = {
            "span_id": self._span_id,
            "duration_ms": round(duration_ms, 3),
            **self._meta,
        }
        if self._explicit_parent_span_id is not None:
            meta["parent_span_id"] = self._explicit_parent_span_id
        meta.setdefault("kind", "span")
        if self._capture_state is not None and self._state_before is not _UNCAPTURED:
            state_after = _snapshot(self._capture_state)
            if state_after is not _UNCAPTURED:
                diff = _state_diff(self._state_before, state_after)
                if diff is not None:
                    meta.setdefault("state_diff", diff)
        if exc_type is not None:
            meta["error"] = f"{exc_type.__name__}: {exc}"

        level = Level.ERROR if exc_type is not None else Level.INFO
        self._logger._log(level, self._name, meta)
