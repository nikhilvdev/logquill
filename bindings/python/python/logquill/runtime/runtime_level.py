"""Changing a `Logger`'s level at runtime, without restarting the process.

Three independent mechanisms, matching what a deployment commonly has
available — pick whichever fits; none of them needs the other two:

- `install_signal_level_handler` — an OS signal (`kill -USR1 <pid>`)
  re-reads an environment variable and applies it.
- `LevelFileWatcher` — a background thread polls a file an orchestrator
  (or you, by hand) writes a level name into.
- `LevelEnvWatcher` — a background thread polls an environment variable
  something *inside* this same process updates (a config-reload callback,
  `python-dotenv` with `override=True`, ...). An OS-level environment
  change made from *outside* the process is never visible to code already
  running inside it — that's a real, unavoidable limit of what an
  environment variable is, not a gap in this watcher; use the file or
  signal mechanism for anything triggered externally.

An unrecognized or missing value is ignored with a warning in every case —
none of these may ever raise out of a signal handler or a background
thread.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from pathlib import Path
from typing import Callable

from logquill.core.logger import Logger

_logger = logging.getLogger("logquill")


def _apply_level(logger: Logger, raw: str | None, *, source: str) -> None:
    if raw is None:
        return
    value = raw.strip()
    if not value:
        return
    try:
        # A file or variable holds text, so a weight arrives as "30": read it as the number it is.
        logger.set_level(int(value) if value.lstrip("-").isdigit() else value)
    except (TypeError, ValueError):
        _logger.warning(
            "logquill: ignoring %s=%r for logger %r — not a valid level "
            "name (TRACE, DEBUG, INFO, WARN, ERROR, FATAL) or number",
            source,
            raw,
            logger.name,
        )


def install_signal_level_handler(
    logger: Logger,
    *,
    signum: int | None = None,
    env_var: str = "LOGQUILL_LEVEL",
) -> Callable[[], None]:
    """Registers a handler so sending `signum` to this process (default
    `SIGUSR1` — `kill -USR1 <pid>`) re-reads `env_var` and applies it via
    `logger.set_level()`.

    Returns a function that uninstalls the handler, restoring whatever
    handler was registered for `signum` before this call — safe to install
    for more than one logger and uninstall in any order, since each call
    only remembers and restores its own previous handler.

    Python signal handlers only run on the main thread, and only the main
    thread may call `signal.signal()` — call this from your process's main
    thread, typically near startup. Raises `RuntimeError` on a platform
    with no `SIGUSR1` (Windows) unless you pass an explicit `signum`.
    """
    if signum is None:
        try:
            signum = signal.SIGUSR1
        except AttributeError:
            raise RuntimeError(
                "install_signal_level_handler(): this platform has no SIGUSR1 "
                "(Windows) — pass an explicit signum, or use LevelFileWatcher/"
                "LevelEnvWatcher instead, which don't need a POSIX signal"
            ) from None

    previous = signal.getsignal(signum)

    def handler(received_signum: int, frame: object) -> None:
        _apply_level(logger, os.environ.get(env_var), source=env_var)

    signal.signal(signum, handler)

    def uninstall() -> None:
        signal.signal(signum, previous)

    return uninstall


class _PollingLevelWatcher:
    """Shared polling loop behind `LevelFileWatcher`/`LevelEnvWatcher`: a
    daemon thread that calls `read()` every `poll_interval` seconds and
    applies the result if it changed since the last check."""

    def __init__(
        self,
        logger: Logger,
        *,
        poll_interval: float,
        source_name: str,
        read: Callable[[], str | None],
    ) -> None:
        self._logger = logger
        self._poll_interval = poll_interval
        self._source_name = source_name
        self._read = read
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_seen: str | None = None

    def poll_once(self) -> None:
        """Checks the source once and applies it if it changed since the
        last check — the single unit of work `start()`'s background loop
        repeats. Exposed directly so a test (or a caller on its own
        schedule) can drive this deterministically, with no thread or
        sleep involved."""
        raw = self._read()
        if raw != self._last_seen:
            self._last_seen = raw
            _apply_level(self._logger, raw, source=self._source_name)

    def start(self) -> None:
        """Starts the background polling thread. Idempotent: calling this
        again while already running does nothing."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="logquill-level-watch", daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = 5.0) -> None:
        """Stops the background thread, waiting up to `timeout` seconds for
        it to actually exit. Safe to call even if never started, or more
        than once."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.poll_once()
            self._stop_event.wait(self._poll_interval)


class LevelFileWatcher(_PollingLevelWatcher):
    """Polls `path`'s content (expected to be a bare level name like
    `"DEBUG"`, or a number) every `poll_interval` seconds, applying it via
    `logger.set_level()` whenever it changes — for an orchestrator (or you,
    by hand: `echo DEBUG > /tmp/app.level`) that can write a file but not
    send a signal.

    A missing file reads as `None` — not an error — so starting the
    watcher before the file exists, or deleting it later, doesn't raise;
    it's simply treated as "no change" until the file reappears with new
    content.
    """

    def __init__(self, logger: Logger, path: str | Path, *, poll_interval: float = 2.0) -> None:
        self._path = Path(path)

        def read() -> str | None:
            try:
                return self._path.read_text(encoding="utf-8")
            except OSError:
                return None

        super().__init__(logger, poll_interval=poll_interval, source_name=str(path), read=read)


class LevelEnvWatcher(_PollingLevelWatcher):
    """Polls `os.environ[env_var]` every `poll_interval` seconds, applying
    it via `logger.set_level()` whenever it changes.

    Only helps when something *inside this same process* is the one
    changing the environment (a config-reload callback,
    `python-dotenv.load_dotenv(override=True)`, your own code doing
    `os.environ[...] = ...`) — an environment variable changed from
    *outside* the process, the way a shell or orchestrator normally would,
    is never visible to code already running inside it. Use
    `LevelFileWatcher` or `install_signal_level_handler` for anything
    triggered externally.
    """

    def __init__(self, logger: Logger, env_var: str, *, poll_interval: float = 2.0) -> None:
        def read() -> str | None:
            return os.environ.get(env_var)

        super().__init__(logger, poll_interval=poll_interval, source_name=env_var, read=read)
