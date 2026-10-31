from __future__ import annotations

import threading
from collections import OrderedDict


class RetryTracker:
    """Counts how many times the same tool call has been reopened, so
    `meta.retry_count` can be stamped on `.action()` records automatically — a
    retry loop is one of the commonest agent failure modes and is otherwise
    invisible in the logs.

    A tool call is identified by `(enclosing span, tool name, tool call id)`.
    The first `.action()` for it is attempt 0; each further `.action()` for the
    same call before it succeeds is a retry, so it comes back as 1, 2, 3, ... A
    successful `.observation()` for the call ends the chain: the next action
    for that tool starts again at 0, which is what stops a loop that
    legitimately calls one tool ten times from being reported as ten retries.

    Bounded: at most `max_entries` calls are remembered, the oldest forgotten
    first, so a long-running process can't grow it without limit.
    """

    def __init__(self, max_entries: int = 1024) -> None:
        """`max_entries` is how many distinct in-flight tool calls to remember."""
        if max_entries < 1:
            raise ValueError(f"max_entries must be >= 1, got {max_entries}")
        self.max_entries = max_entries
        self._attempts: OrderedDict[tuple[str, str, str], int] = OrderedDict()
        self._lock = threading.Lock()

    def opened(self, span_id: str | None, tool: str, call_id: str | None = None) -> int:
        """Record another action for this call; returns its retry count (0 for
        the first attempt)."""
        key = (span_id or "", tool, call_id or "")
        with self._lock:
            count = self._attempts.get(key, -1) + 1
            self._attempts[key] = count
            self._attempts.move_to_end(key)
            while len(self._attempts) > self.max_entries:
                self._attempts.popitem(last=False)
            return count

    def succeeded(self, span_id: str | None, tool: str, call_id: str | None = None) -> None:
        """The call finished successfully; forget it."""
        with self._lock:
            self._attempts.pop((span_id or "", tool, call_id or ""), None)
