from __future__ import annotations

import logging
import os
import signal
import time
from pathlib import Path

import pytest

from logquill import Level, Logger
from logquill.runtime.runtime_level import (
    LevelEnvWatcher,
    LevelFileWatcher,
    install_signal_level_handler,
)

# --- install_signal_level_handler --------------------------------------------


def test_sending_the_signal_applies_the_env_vars_level(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = Logger("app", level="INFO")
    monkeypatch.setenv("TEST_LOGQUILL_LEVEL", "DEBUG")
    uninstall = install_signal_level_handler(
        logger, signum=signal.SIGUSR1, env_var="TEST_LOGQUILL_LEVEL"
    )
    try:
        os.kill(os.getpid(), signal.SIGUSR1)
    finally:
        uninstall()

    assert logger.level == Level.DEBUG


def test_uninstall_restores_the_previous_handler() -> None:
    logger = Logger("app")
    sentinel_calls = []
    original = signal.signal(signal.SIGUSR1, lambda *_: sentinel_calls.append(1))
    try:
        uninstall = install_signal_level_handler(logger, signum=signal.SIGUSR1)
        uninstall()

        os.kill(os.getpid(), signal.SIGUSR1)
        time.sleep(0.05)

        assert sentinel_calls == [1]  # the original handler ran, not logquill's
    finally:
        signal.signal(signal.SIGUSR1, original)


def test_missing_env_var_on_signal_leaves_the_level_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = Logger("app", level="WARN")
    monkeypatch.delenv("TEST_LOGQUILL_LEVEL_UNSET", raising=False)
    uninstall = install_signal_level_handler(
        logger, signum=signal.SIGUSR1, env_var="TEST_LOGQUILL_LEVEL_UNSET"
    )
    try:
        os.kill(os.getpid(), signal.SIGUSR1)
    finally:
        uninstall()

    assert logger.level == Level.WARN


def test_an_invalid_level_value_warns_and_leaves_the_level_unchanged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    logger = Logger("app", level="WARN")
    monkeypatch.setenv("TEST_LOGQUILL_LEVEL", "NOT_A_LEVEL")
    uninstall = install_signal_level_handler(
        logger, signum=signal.SIGUSR1, env_var="TEST_LOGQUILL_LEVEL"
    )
    try:
        with caplog.at_level(logging.WARNING, logger="logquill"):
            os.kill(os.getpid(), signal.SIGUSR1)
    finally:
        uninstall()

    assert logger.level == Level.WARN
    assert any("not a valid level" in r.getMessage() for r in caplog.records)


def test_without_sigusr1_on_this_platform_raises_unless_an_explicit_signum_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import logquill.runtime.runtime_level as module

    class NoSigUsr1:
        def getsignal(self, *a: object) -> object:
            return signal.SIG_DFL

        def signal(self, *a: object) -> object:
            return signal.SIG_DFL

    monkeypatch.setattr(module, "signal", NoSigUsr1())
    logger = Logger("app")

    with pytest.raises(RuntimeError, match="SIGUSR1"):
        install_signal_level_handler(logger)

    # an explicit signum sidesteps the missing SIGUSR1 entirely
    uninstall = install_signal_level_handler(logger, signum=signal.SIGUSR1)
    uninstall()


# --- _PollingLevelWatcher / LevelFileWatcher ---------------------------------


def test_file_watcher_applies_the_files_content(tmp_path: Path) -> None:
    logger = Logger("app", level="INFO")
    path = tmp_path / "level.txt"
    path.write_text("DEBUG")
    watcher = LevelFileWatcher(logger, path)

    watcher.poll_once()

    assert logger.level == Level.DEBUG


def test_file_watcher_before_the_file_exists_is_a_no_op() -> None:
    logger = Logger("app", level="WARN")
    watcher = LevelFileWatcher(logger, "/no/such/file/at/all.txt")

    watcher.poll_once()

    assert logger.level == Level.WARN


def test_file_watcher_only_reapplies_on_an_actual_change(tmp_path: Path) -> None:
    logger = Logger("app", level="INFO")
    path = tmp_path / "level.txt"
    path.write_text("DEBUG")
    watcher = LevelFileWatcher(logger, path)
    watcher.poll_once()
    logger.set_level("WARN")  # someone else changes it in between

    watcher.poll_once()  # file content unchanged since last poll

    assert logger.level == Level.WARN  # not stomped back to DEBUG


def test_file_watcher_picks_up_a_later_change(tmp_path: Path) -> None:
    logger = Logger("app", level="INFO")
    path = tmp_path / "level.txt"
    path.write_text("DEBUG")
    watcher = LevelFileWatcher(logger, path)
    watcher.poll_once()

    path.write_text("ERROR")
    watcher.poll_once()

    assert logger.level == Level.ERROR


def test_file_watcher_tolerates_a_trailing_newline(tmp_path: Path) -> None:
    logger = Logger("app")
    path = tmp_path / "level.txt"
    path.write_text("DEBUG\n")
    watcher = LevelFileWatcher(logger, path)

    watcher.poll_once()

    assert logger.level == Level.DEBUG


def test_file_watcher_start_and_stop_run_a_real_background_thread(tmp_path: Path) -> None:
    logger = Logger("app", level="INFO")
    path = tmp_path / "level.txt"
    watcher = LevelFileWatcher(logger, path, poll_interval=0.02)
    watcher.start()
    try:
        path.write_text("TRACE")
        for _ in range(100):
            if logger.level == Level.TRACE:
                break
            time.sleep(0.01)
        assert logger.level == Level.TRACE
    finally:
        watcher.stop()


def test_start_is_idempotent_and_stop_is_safe_without_start(tmp_path: Path) -> None:
    logger = Logger("app")
    watcher = LevelFileWatcher(logger, tmp_path / "level.txt", poll_interval=0.02)

    watcher.start()
    watcher.start()  # no second thread, no error
    watcher.stop()
    watcher.stop()  # safe to call again

    never_started = LevelFileWatcher(logger, tmp_path / "level.txt")
    never_started.stop()  # safe even though start() was never called


# --- LevelEnvWatcher ----------------------------------------------------------


def test_env_watcher_applies_a_changed_value(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = Logger("app", level="INFO")
    monkeypatch.setenv("TEST_LOGQUILL_ENV_LEVEL", "ERROR")
    watcher = LevelEnvWatcher(logger, "TEST_LOGQUILL_ENV_LEVEL")

    watcher.poll_once()

    assert logger.level == Level.ERROR


def test_env_watcher_with_no_var_set_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = Logger("app", level="WARN")
    monkeypatch.delenv("TEST_LOGQUILL_ENV_LEVEL_UNSET", raising=False)
    watcher = LevelEnvWatcher(logger, "TEST_LOGQUILL_ENV_LEVEL_UNSET")

    watcher.poll_once()

    assert logger.level == Level.WARN


def test_env_watcher_only_reapplies_on_an_actual_change(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = Logger("app", level="INFO")
    monkeypatch.setenv("TEST_LOGQUILL_ENV_LEVEL", "DEBUG")
    watcher = LevelEnvWatcher(logger, "TEST_LOGQUILL_ENV_LEVEL")
    watcher.poll_once()
    logger.set_level("ERROR")

    watcher.poll_once()  # unchanged since last poll

    assert logger.level == Level.ERROR
