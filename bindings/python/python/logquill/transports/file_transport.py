from __future__ import annotations

from pathlib import Path
from typing import Literal

from logquill import _native
from logquill.core.records import LogRecord
from logquill.formatters import Formatter, native_formatter_name
from logquill.transports.transport import Transport


class FileTransport(Transport):
    """Appends formatted records to a file, rotating when it exceeds `max_bytes`. The work is done
    in Rust.

    Pass `encrypt_key` (a Fernet key, URL-safe base64 of 32 bytes: `logquill.generate_fernet_key()`
    or `cryptography.fernet.Fernet.generate_key()`) to encrypt each line before writing; a local log
    file on disk usually isn't encrypted the way a cloud sink is. The file holds one Fernet token
    per line, readable by any Fernet implementation, and no `cryptography` package is needed to
    write it. `compression="gz"` or `"zip"` compresses each file as it is rotated out.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        formatter: Formatter | None = None,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
        encrypt_key: bytes | str | None = None,
        compression: Literal["gz", "zip"] | None = None,
    ) -> None:
        """`max_bytes` triggers rotation once the file reaches that size (0 disables rotation);
        `backup_count` bounds how many rotated files are kept (`path.1`, `path.2`, ...); with 0 the
        file is simply deleted on rotation. `encrypt_key` opts into per-line Fernet encryption."""
        super().__init__(formatter)
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        key = encrypt_key.decode("ascii") if isinstance(encrypt_key, bytes) else encrypt_key
        try:
            self._native = _native.file_transport(
                str(self.path),
                max_bytes,
                backup_count,
                native_formatter_name(formatter) or "json",
                compression,
                key,
            )
        except ValueError as error:
            raise ValueError(f"FileTransport: {error}") from error
        self._native_dispatch = self._native if native_formatter_name(formatter) else None

    def write(self, formatted: str, record: LogRecord) -> None:
        """Appends `formatted` as one line (encrypted first if `encrypt_key` was set), and rotates
        if `max_bytes` has been reached."""
        self._native.write(formatted, record)

    def flush(self) -> None:
        """Flushes the file."""
        self._native.flush()

    def close(self) -> None:
        """Closes the underlying file handle."""
        self._native.close()
