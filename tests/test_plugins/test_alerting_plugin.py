from __future__ import annotations

import threading
import time

from logquill.core.logger import Logger
from logquill.core.records import LogRecord
from logquill.plugins.alerting_plugin import AlertingPlugin

_POLL_TIMEOUT = 2.0
_POLL_INTERVAL = 0.01


def _wait_until(predicate: object, timeout: float = _POLL_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(_POLL_INTERVAL)
    return predicate()  # type: ignore[operator]


class RecordingAlertPlugin(AlertingPlugin):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.sent: list[tuple[LogRecord, int]] = []
        self._sent_lock = threading.Lock()

    def send_alert(self, record: LogRecord, occurrences: int) -> None:
        with self._sent_lock:
            self.sent.append((record, occurrences))

    def sent_count(self) -> int:
        with self._sent_lock:
            return len(self.sent)


class BrokenAlertPlugin(AlertingPlugin):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.errors: list[Exception] = []

    def send_alert(self, record: LogRecord, occurrences: int) -> None:
        raise RuntimeError("destination unreachable")

    def on_error(self, exc: Exception, record: LogRecord) -> None:
        self.errors.append(exc)


def test_error_level_record_fires_an_alert() -> None:
    alerting = RecordingAlertPlugin(dedupe_window_seconds=60)
    logger = Logger("app.test", plugins=[alerting])

    logger.error("something broke")

    assert _wait_until(lambda: alerting.sent_count() >= 1)
    record, occurrences = alerting.sent[0]
    assert record["message"] == "something broke"
    assert occurrences == 1


def test_below_threshold_records_do_not_fire_an_alert() -> None:
    alerting = RecordingAlertPlugin(dedupe_window_seconds=60)
    logger = Logger("app.test", plugins=[alerting])

    logger.info("just info")
    logger.warn("just a warning")
    time.sleep(0.1)

    assert alerting.sent_count() == 0


def test_never_blocks_the_caller_even_when_destination_is_unreachable() -> None:
    broken = BrokenAlertPlugin(dedupe_window_seconds=60)
    logger = Logger("app.test", plugins=[broken])

    record = logger.error("boom")  # must return promptly, not hang or raise

    assert record is not None
    assert _wait_until(lambda: len(broken.errors) >= 1)
    assert isinstance(broken.errors[0], RuntimeError)


def test_duplicate_errors_within_window_collapse_into_one_followup_alert() -> None:
    alerting = RecordingAlertPlugin(dedupe_window_seconds=0.1)
    logger = Logger("app.test", plugins=[alerting])

    for _ in range(5):
        logger.error("repeated failure")

    # first occurrence sends immediately with occurrences=1
    assert _wait_until(lambda: alerting.sent_count() >= 1)
    # once the window closes, exactly one follow-up alert reports the total
    assert _wait_until(lambda: alerting.sent_count() >= 2, timeout=2.0)
    time.sleep(0.2)
    assert alerting.sent_count() == 2
    _, first_occurrences = alerting.sent[0]
    _, followup_occurrences = alerting.sent[1]
    assert first_occurrences == 1
    assert followup_occurrences == 5


def test_a_single_occurrence_gets_no_followup_alert() -> None:
    alerting = RecordingAlertPlugin(dedupe_window_seconds=0.05)
    logger = Logger("app.test", plugins=[alerting])

    logger.error("one-off failure")

    assert _wait_until(lambda: alerting.sent_count() >= 1)
    time.sleep(0.2)  # let the dedupe window close
    assert alerting.sent_count() == 1


def test_new_dedupe_keys_beyond_max_tracked_keys_are_dropped() -> None:
    # Bounded memory: once max_tracked_keys concurrent dedupe windows are
    # open, a new distinct key is dropped outright rather than tracked or
    # sent — alerting degrades under extreme cardinality, logging itself
    # never does.
    alerting = RecordingAlertPlugin(dedupe_window_seconds=60, max_tracked_keys=1)
    logger = Logger("app.test", plugins=[alerting])

    logger.error("first distinct error")
    assert _wait_until(lambda: alerting.sent_count() >= 1)

    # second, different error while the first key's window is still open
    # (max_tracked_keys=1) — dropped, not sent
    logger.error("second distinct error")
    time.sleep(0.2)

    assert alerting.sent_count() == 1


def test_close_cancels_pending_dedupe_timers() -> None:
    alerting = RecordingAlertPlugin(dedupe_window_seconds=60)
    logger = Logger("app.test", plugins=[alerting])

    logger.error("first")
    logger.error("first")  # buffered as a pending follow-up
    assert _wait_until(lambda: alerting.sent_count() >= 1)

    alerting.close()

    assert alerting._windows == {}
