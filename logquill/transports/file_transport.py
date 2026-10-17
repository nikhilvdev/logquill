from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, BinaryIO, TextIO, cast

from logquill.core.records import LogRecord
from logquill.formatters import Formatter
from logquill.transports.transport import Transport


def _load_fernet(encrypt_key: bytes | str) -> Any:
    try:
        from cryptography.fernet import Fernet  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(
            "FileTransport(encrypt_key=...) requires the optional `cryptography` "
            "dependency — install with `pip install logquill[crypto]`."
        ) from exc
    key = encrypt_key.encode("utf-8") if isinstance(encrypt_key, str) else encrypt_key
    try:
        return Fernet(key)
    except Exception as exc:
        raise ValueError(
            "FileTransport(encrypt_key=...): not a valid Fernet key — generate one "
            "with `cryptography.fernet.Fernet.generate_key()`."
        ) from exc


class FileTransport(Transport):
    """Appends formatted records to a file, rotating when it exceeds `max_bytes`.

    Pass `encrypt_key` (a `cryptography.fernet.Fernet` key, from
    `Fernet.generate_key()`) to encrypt each line before writing — worth
    doing here specifically because cloud transports typically already
    encrypt server-side, but a local log file on disk usually doesn't.
    Requires the optional `cryptography` dependency
    (`pip install logquill[crypto]`), imported lazily so `FileTransport`
    has zero dependencies as long as `encrypt_key` stays unset. Encrypted
    files are append-only ciphertext, one Fernet token per line — not
    human-readable, and not `FileTransport`'s job to decrypt back; do that
    with the same key via `Fernet.decrypt()` per line when reading.

    `write()` locks around the write-and-maybe-rotate sequence: with
    `async_dispatch=False` (the default), a `Logger` shared across
    application threads would otherwise let `_rotate()` close and reopen
    the file handle while another thread's `write()` is still using the old
    one — not just interleaved lines, but a write to an already-closed file.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        formatter: Formatter | None = None,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
        encrypt_key: bytes | str | None = None,
    ) -> None:
        """`max_bytes` triggers rotation once the file reaches that size
        (0 disables rotation); `backup_count` bounds how many rotated files
        are kept (`path.1`, `path.2`, ...) — the oldest is deleted once that
        many already exist, or, if `backup_count` is 0, the file is simply
        truncated on rotation rather than kept. `encrypt_key` opts into
        per-line Fernet encryption — see the class docstring."""
        super().__init__(formatter)
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._fernet: Any = _load_fernet(encrypt_key) if encrypt_key is not None else None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file: TextIO | BinaryIO = self._open()
        self._write_lock = threading.Lock()

    def _open(self) -> TextIO | BinaryIO:
        if self._fernet is not None:
            return self.path.open("ab")
        return self.path.open("a", encoding="utf-8")

    def write(self, formatted: str, record: LogRecord) -> None:
        """Appends `formatted` as one line (encrypted first if `encrypt_key`
        was set), flushes to disk immediately, and rotates if `max_bytes`
        has been reached."""
        with self._write_lock:
            if self._fernet is not None:
                cast(BinaryIO, self._file).write(
                    self._fernet.encrypt(formatted.encode("utf-8")) + b"\n"
                )
            else:
                cast(TextIO, self._file).write(formatted + "\n")
            self._file.flush()
            if 0 < self.max_bytes <= self._file.tell():
                self._rotate()

    def _rotate(self) -> None:
        self._file.close()
        if self.backup_count > 0:
            for index in range(self.backup_count - 1, 0, -1):
                src = self.path.with_name(f"{self.path.name}.{index}")
                dst = self.path.with_name(f"{self.path.name}.{index + 1}")
                if src.exists():
                    dst.unlink(missing_ok=True)
                    src.rename(dst)
            backup = self.path.with_name(f"{self.path.name}.1")
            backup.unlink(missing_ok=True)
            self.path.rename(backup)
        else:
            self.path.unlink(missing_ok=True)
        self._file = self._open()

    def close(self) -> None:
        """Closes the underlying file handle."""
        with self._write_lock:
            self._file.close()
