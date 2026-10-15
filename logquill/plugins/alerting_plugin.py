from __future__ import annotations

import contextlib
import threading
from typing import Callable

from logquill.core.levels import Level, parse_level
from logquill.core.records import LogRecord
from logquill.plugins.plugin import Plugin


class _Window:
    """Tracks one open dedupe window: the first matching record, how many
    matches have arrived since, and the timer that will flush it."""

    __slots__ = ("record", "count", "timer")

    def __init__(self, record: LogRecord, timer: threading.Timer) -> None:
        """`record` is the first record seen for this dedupe key; `timer`
        is the pending flush that will send a follow-up alert if `count`
        ends up greater than 1 by the time it fires."""
        self.record = record
        self.count = 1
        self.timer = timer


class AlertingPlugin(Plugin):
    """Base class for plugins that fire an external alert on ERROR/FATAL (or
    any configurable `threshold`).

    A concrete subclass implements only `send_alert(record, occurrences)` —
    everything else (thresholding, deduplication, never blocking the
    caller, never letting a broken destination crash logging) lives here.

    The first record at or above `threshold` for a given dedupe key (by
    default: level + logger + message) fires `send_alert` right away, on a
    short-lived background thread — so the log call that triggered it is
    never blocked on a webhook, SMTP handshake, or any other I/O, even if
    the destination is slow or unreachable. This plugin spawns its own
    thread per alert rather than routing through `Logger`'s shared
    `AsyncWorker` (see `logquill/worker.py`), since a plugin hook runs
    before dispatch is decided and has no handle on that queue; unifying
    the two is a possible future simplification, not a correctness gap.

    Any further record matching the same dedupe key within
    `dedupe_window_seconds` of the first is *not* sent again — it just
    increments a counter. When the window closes, if more than one record
    matched, exactly one follow-up alert is sent with the total occurrence
    count, instead of spamming the destination once per record. Tracking is
    bounded to `max_tracked_keys` distinct concurrent dedupe keys; beyond
    that, new keys are dropped rather than tracked (alerting degrades under
    extreme cardinality, logging itself never does).

    `send_alert` is always called from a background thread and is wrapped
    so an exception in it can't crash that thread or the caller — it's
    routed to this plugin's own `on_error`, the same as any other plugin
    hook that raises.
    """

    def __init__(
        self,
        *,
        threshold: int | str | Level = Level.ERROR,
        dedupe_window_seconds: float = 300.0,
        dedupe_key: Callable[[LogRecord], str] | None = None,
        max_tracked_keys: int = 500,
    ) -> None:
        """`dedupe_key` defaults to level+logger+message; pass a custom
        function to group differently (e.g. by `meta["trace_id"]`).
        `max_tracked_keys` bounds how many distinct dedupe windows are open
        at once — beyond that, a new key is dropped from tracking rather
        than alerted on, so alerting degrades under extreme cardinality
        instead of growing memory without bound."""
        self.threshold = parse_level(threshold)
        self.dedupe_window_seconds = dedupe_window_seconds
        self._dedupe_key = dedupe_key or self._default_dedupe_key
        self.max_tracked_keys = max_tracked_keys
        self._lock = threading.Lock()
        self._windows: dict[str, _Window] = {}

    @staticmethod
    def _default_dedupe_key(record: LogRecord) -> str:
        return f"{record['level']}:{record['logger']}:{record['message']}"

    def after_log(self, record: LogRecord) -> None:
        """Fires `send_alert` on a new background thread for the first
        record at or above `threshold` under a given dedupe key, and starts
        that key's dedupe window; any further match within the window just
        increments its count instead of sending again."""
        if Level[record["level"]] < self.threshold:
            return

        key = self._dedupe_key(record)
        with self._lock:
            window = self._windows.get(key)
            if window is not None:
                window.count += 1
                return

            if len(self._windows) >= self.max_tracked_keys:
                return

            timer = threading.Timer(self.dedupe_window_seconds, self._flush, args=(key,))
            timer.daemon = True
            self._windows[key] = _Window(record, timer)
            timer.start()

        threading.Thread(target=self._safe_send, args=(record, 1), daemon=True).start()

    def _flush(self, key: str) -> None:
        with self._lock:
            window = self._windows.pop(key, None)
        if window is None or window.count <= 1:
            return
        self._safe_send(window.record, window.count)

    def _safe_send(self, record: LogRecord, occurrences: int) -> None:
        try:
            self.send_alert(record, occurrences)
        except Exception as exc:
            with contextlib.suppress(Exception):
                self.on_error(exc, record)

    def send_alert(self, record: LogRecord, occurrences: int) -> None:
        """Send one alert for `record`, representing `occurrences` collapsed
        duplicates (1 on first occurrence; the deduped total on a follow-up
        flush). Override in a concrete subclass — never call this directly,
        `AlertingPlugin` calls it from a background thread.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Cancel any pending dedupe-window timers. Call on logger shutdown."""
        with self._lock:
            windows = list(self._windows.values())
            self._windows.clear()
        for window in windows:
            window.timer.cancel()
