import random
import re
import threading
import time
from datetime import datetime, timezone

import pytest

from logquill.core import records

_ISO_MS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def _reference(epoch: float) -> str:
    # the straightforward, uncached formatting the cached version must match
    moment = datetime.fromtimestamp(epoch, timezone.utc)
    return f"{moment.strftime('%Y-%m-%dT%H:%M:%S')}.{moment.microsecond // 1000:03d}Z"


def test_timestamp_is_iso8601_utc_with_milliseconds() -> None:
    assert _ISO_MS.match(records.utc_timestamp())


@pytest.mark.parametrize(
    "epoch",
    [
        0.0,  # the epoch itself
        1_700_000_000.123,
        1_767_225_599.999,  # last millisecond of 2025
        1_767_225_600.0,  # first moment of 2026
        1_782_777_600.5,  # a mid-year date
    ],
)
def test_timestamp_matches_plain_formatting(epoch: float, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(records.time, "time", lambda: epoch)

    assert records.utc_timestamp() == _reference(epoch)


def test_timestamp_matches_plain_formatting_for_many_random_instants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rng = random.Random(20261018)
    instants = [rng.uniform(0, 4_000_000_000) for _ in range(20_000)]
    # just below a whole second, where rounding to microseconds carries over
    instants += [float(second) - 1e-7 for second in range(1_767_225_600, 1_767_225_700)]
    clock = [0.0]
    monkeypatch.setattr(records.time, "time", lambda: clock[0])

    for epoch in instants:
        clock[0] = epoch
        assert records.utc_timestamp() == _reference(epoch), epoch


def test_timestamp_rolls_over_when_the_second_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1_767_225_599.9]
    monkeypatch.setattr(records.time, "time", lambda: clock[0])

    before = records.utc_timestamp()
    clock[0] = 1_767_225_600.1
    after = records.utc_timestamp()

    assert before.startswith("2025-12-31T23:59:59.")
    assert after.startswith("2026-01-01T00:00:00.")


def test_timestamps_never_go_backwards_across_threads() -> None:
    # many threads hitting the one-second cache at once must each get a
    # well-formed timestamp, never a prefix from one second with another's ms
    results: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        local = [records.utc_timestamp() for _ in range(2000)]
        with lock:
            results.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    start = time.time()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    end = time.time()

    assert len(results) == 16_000
    assert all(_ISO_MS.match(stamp) for stamp in results)
    low, high = _reference(start - 1), _reference(end + 1)
    assert all(low <= stamp <= high for stamp in results)
