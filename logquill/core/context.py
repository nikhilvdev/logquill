from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import Any, Iterator

_current_context: ContextVar[dict[str, Any] | None] = ContextVar("logquill_context", default=None)


def current_context() -> dict[str, Any]:
    """The merged key/value pairs bound by every `bind_context()` block
    currently active in this execution context (thread or asyncio task).
    """
    return _current_context.get() or {}


@contextlib.contextmanager
def bind_context(**values: Any) -> Iterator[None]:
    """Merge `values` into the request-scoped context for the duration of
    this `with` block — every `Logger` call underneath it, through any
    method and any number of function calls deep, picks them up in `meta`
    automatically, without threading them through every signature by hand:

        with bind_context(request_id="abc123"):
            handle_request()  # any logging in here, or in what it calls,
                               # gets meta["request_id"] = "abc123" for free

    Backed by a `contextvars.ContextVar`, so concurrent asyncio tasks and
    threads each see their own bound context — binding in one never leaks
    into another. Blocks nest by merging (an inner `bind_context` value
    wins over an outer one on key collision, the same way an explicit
    call-site `meta` value wins over anything bound here); exiting restores
    exactly the context that was active before the block started.
    """
    parent = current_context()
    token = _current_context.set({**parent, **values})
    try:
        yield
    finally:
        _current_context.reset(token)
