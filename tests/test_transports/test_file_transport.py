import sys
from pathlib import Path

import pytest

from logquill.core.levels import Level
from logquill.core.logger import Logger
from logquill.core.records import create_record
from logquill.transports.file_transport import FileTransport


def test_writes_are_appended_to_the_file(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    transport = FileTransport(log_path)
    logger = Logger("app.test", transports=[transport])

    logger.info("first")
    logger.info("second")
    transport.close()

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "first" in lines[0]
    assert "second" in lines[1]


def test_rotation_moves_current_file_to_backup(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    transport = FileTransport(log_path, max_bytes=1, backup_count=2)
    logger = Logger("app.test", transports=[transport])

    logger.info("first")
    logger.info("second")
    transport.close()

    assert (tmp_path / "app.log.1").exists()
    assert log_path.exists()


def test_creates_parent_directories(tmp_path: Path) -> None:
    log_path = tmp_path / "nested" / "dir" / "app.log"
    transport = FileTransport(log_path)
    record = create_record(level=Level.INFO, logger="app.test", message="hi", meta={})

    transport.write(transport.format(record), record)
    transport.close()

    assert log_path.exists()


def test_encrypt_key_without_cryptography_installed_raises_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Forces the lazy `from cryptography.fernet import Fernet` inside
    # `_load_fernet` to fail regardless of whether the real (optional)
    # dependency happens to be installed in this environment.
    monkeypatch.setitem(sys.modules, "cryptography", None)
    monkeypatch.setitem(sys.modules, "cryptography.fernet", None)

    with pytest.raises(ImportError, match=r"logquill\[crypto\]"):
        FileTransport(tmp_path / "app.log", encrypt_key=b"anything")


def test_invalid_encrypt_key_raises_a_clear_error(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")

    with pytest.raises(ValueError, match="not a valid Fernet key"):
        FileTransport(tmp_path / "app.log", encrypt_key=b"too-short")


def test_encrypted_lines_are_not_plaintext_and_decrypt_back(tmp_path: Path) -> None:
    fernet = pytest.importorskip("cryptography.fernet")

    key = fernet.Fernet.generate_key()
    log_path = tmp_path / "app.log"
    transport = FileTransport(log_path, encrypt_key=key)
    logger = Logger("app.test", transports=[transport])

    logger.info("a secret message", user_id=42)
    transport.close()

    raw = log_path.read_bytes()
    assert b"secret message" not in raw
    assert b"user_id" not in raw

    f = fernet.Fernet(key)
    lines = raw.splitlines()
    assert len(lines) == 1
    decrypted = f.decrypt(lines[0]).decode("utf-8")
    assert "a secret message" in decrypted
    assert '"user_id":42' in decrypted


def test_encrypt_key_accepts_a_str_key(tmp_path: Path) -> None:
    fernet = pytest.importorskip("cryptography.fernet")

    key = fernet.Fernet.generate_key().decode("ascii")
    log_path = tmp_path / "app.log"
    transport = FileTransport(log_path, encrypt_key=key)
    logger = Logger("app.test", transports=[transport])

    logger.info("hello")
    transport.close()

    decrypted = fernet.Fernet(key).decrypt(log_path.read_bytes().strip())
    assert b"hello" in decrypted


def test_rotation_works_with_encryption_enabled(tmp_path: Path) -> None:
    fernet = pytest.importorskip("cryptography.fernet")

    key = fernet.Fernet.generate_key()
    log_path = tmp_path / "app.log"
    transport = FileTransport(log_path, max_bytes=1, backup_count=2, encrypt_key=key)
    logger = Logger("app.test", transports=[transport])

    logger.info("first")
    logger.info("second")
    transport.close()

    assert (tmp_path / "app.log.1").exists()
    assert log_path.exists()
