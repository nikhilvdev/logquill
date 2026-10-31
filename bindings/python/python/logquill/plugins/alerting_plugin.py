from __future__ import annotations

import contextlib
import logging
import threading
from typing import Any, Callable

from logquill import _native

from logquill.core.levels import Level, parse_level
from logquill.core.records import LogRecord
from logquill.plugins.plugin import Plugin

_logger = logging.getLogger("logquill")


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

    The first record at or above `threshold` for a given dedupe key (by default: level + logger +
    message) is sent right away, on a worker thread in the Rust core — so the log call that triggered
    it is never blocked on a webhook, SMTP handshake, or any other I/O, even if the destination is
    slow or unreachable.

    Any further record matching the same dedupe key within `dedupe_window_seconds` of the first is
    *not* sent again — it just increments a counter. When the window closes, if more than one record
    matched, exactly one follow-up alert is sent with the total occurrence count, instead of
    spamming the destination once per record. Tracking is bounded to `max_tracked_keys` distinct
    concurrent dedupe keys; beyond that an alert is still sent (it is never dropped for lack of
    room), only its repeats are not collapsed. A custom `dedupe_key` function is run in Python and
    uses the plain Python implementation.

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
        #: The Rust dedupe-and-send service, made on first use (a subclass sets its own attributes
        #: after this runs). Not used with a custom `dedupe_key`.
        self._service: Any = None
        self._use_service = dedupe_key is None
        self._sender_object: Any = None

    def _native_sender(self) -> Any:
        """A native sender doing this plugin's delivery, or `None` to call `send_alert` from Python.
        `SlackAlertPlugin`, `PagerDutyAlertPlugin` and `EmailAlertPlugin` override this."""
        return None

    def _alert_sender(self) -> Any:
        if self._sender_object is None:
            self._sender_object = self._native_sender() or _HostSender(self)
        return self._sender_object

    def _get_service(self) -> Any:
        if self._service is None:
            self._service = _native.Alerting(
                self._alert_sender(),
                self.threshold.name,
                self.dedupe_window_seconds,
                self.max_tracked_keys,
                False,
                lambda message: _logger.warning("%s", message),
            )
        return self._service

    @staticmethod
    def _default_dedupe_key(record: LogRecord) -> str:
        return f"{record['level']}:{record['logger']}:{record['message']}"

    def after_log(self, record: LogRecord) -> None:
        """Fires `send_alert` on a new background thread for the first
        record at or above `threshold` under a given dedupe key, and starts
        that key's dedupe window; any further match within the window just
        increments its count instead of sending again. With the default dedupe key this is done by
        the Rust service on its own threads."""
        if self._use_service:
            self._get_service().observe(record)
            return
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
        if self._service is not None:
            self._service.close()
        with self._lock:
            windows = list(self._windows.values())
            self._windows.clear()
        for window in windows:
            window.timer.cancel()


class _HostSender:
    """Lets the Rust service call a Python plugin's `send_alert`, routing its failures to the plugin's
    `on_error` as before."""

    def __init__(self, plugin: AlertingPlugin) -> None:
        self._plugin = plugin

    def send_alert(self, record: LogRecord, occurrences: int) -> None:
        self._plugin._safe_send(record, occurrences)
