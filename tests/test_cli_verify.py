from __future__ import annotations

import io
from pathlib import Path

from logquill import AuditLogger
from logquill.cli.cli import _run_verify, build_parser, main
from logquill.transports.file_transport import FileTransport


def _write_an_audit_log(path: Path) -> AuditLogger:
    logger = AuditLogger("audit", transports=[FileTransport(path)])
    logger.info("login", user_id=1)
    logger.info("logout", user_id=1)
    return logger


def _verify(file: str, *extra_args: str) -> tuple[str, int]:
    args = build_parser().parse_args(["verify", file, *extra_args])
    out = io.StringIO()
    exit_code = _run_verify(args, out=out)
    return out.getvalue(), exit_code


def test_verify_passes_on_an_untampered_file(tmp_path: Path) -> None:
    path = tmp_path / "audit.log"
    _write_an_audit_log(path).close()

    output, exit_code = _verify(str(path))

    assert exit_code == 0
    assert "OK" in output
    assert "2 record(s)" in output
    assert "chain head is" in output


def test_verify_fails_with_an_actionable_message_on_a_tampered_file(tmp_path: Path) -> None:
    path = tmp_path / "audit.log"
    _write_an_audit_log(path).close()
    lines = path.read_text().splitlines()
    lines[0] = lines[0].replace("login", "EDITED")
    path.write_text("\n".join(lines) + "\n")

    output, exit_code = _verify(str(path))

    assert exit_code == 1
    assert "FAILED" in output
    assert "edited after being chained" in output
    assert "record 1" in output


def test_verify_reports_a_helpful_error_for_a_missing_file(tmp_path: Path) -> None:
    output, exit_code = _verify(str(tmp_path / "missing.log"))

    assert exit_code == 1
    assert "no such file" in output


def test_verify_with_a_matching_signature_passes(tmp_path: Path) -> None:
    path = tmp_path / "audit.log"
    logger = _write_an_audit_log(path)
    signature = logger.sign_head(bytes.fromhex("6b31"))
    logger.close()

    output, exit_code = _verify(str(path), "--sign-key", "6b31", "--signature", signature)

    assert exit_code == 0
    assert "OK" in output


def test_verify_with_a_wrong_signature_fails(tmp_path: Path) -> None:
    path = tmp_path / "audit.log"
    _write_an_audit_log(path).close()

    output, exit_code = _verify(str(path), "--sign-key", "6b31", "--signature", "a" * 64)

    assert exit_code == 1
    assert "FAILED" in output
    assert "signature" in output


def test_verify_catches_a_truncated_file_with_the_right_signature(tmp_path: Path) -> None:
    path = tmp_path / "audit.log"
    logger = _write_an_audit_log(path)
    signature = logger.sign_head(bytes.fromhex("6b31"))
    logger.close()
    lines = path.read_text().splitlines()
    path.write_text(lines[0] + "\n")  # drop the last (signed) line

    output, exit_code = _verify(str(path), "--sign-key", "6b31", "--signature", signature)

    assert exit_code == 1
    assert "truncated" in output


def test_sign_key_and_signature_must_be_given_together(tmp_path: Path) -> None:
    path = tmp_path / "audit.log"
    _write_an_audit_log(path).close()

    output, exit_code = _verify(str(path), "--sign-key", "6b31")

    assert exit_code == 1
    assert "together" in output


def test_a_non_hex_sign_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "audit.log"
    _write_an_audit_log(path).close()

    output, exit_code = _verify(str(path), "--sign-key", "not-hex!", "--signature", "a" * 64)

    assert exit_code == 1
    assert "hex" in output


def test_verify_via_main_entry_point(tmp_path: Path) -> None:
    path = tmp_path / "audit.log"
    _write_an_audit_log(path).close()

    assert main(["verify", str(path)]) == 0


def test_verify_on_a_file_with_no_hash_at_all(tmp_path: Path) -> None:
    from logquill import Logger

    path = tmp_path / "plain.log"
    plain = Logger("app", transports=[FileTransport(path)], flush_at_exit=False)
    plain.info("hello")  # never chained — no TamperEvidentPlugin attached
    plain.close()

    output, exit_code = _verify(str(path))

    assert exit_code == 1
    assert "never chained" in output
