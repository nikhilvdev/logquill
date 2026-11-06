"""The `logquill` command: `tail`, `trace`, `dev`, `serve` and `verify`.

The command is the same Rust program as the `logquill-cli` crate, compiled into this package's extension
module, so `pip install logquill` puts a working `logquill` on the path with no other install. Run it as
`logquill tail app.log` or `python -m logquill.cli tail app.log`.
"""

from __future__ import annotations

import signal
import sys

from logquill import _native


def main(argv: list[str] | None = None) -> int:
    """Run the command with `argv` (default `sys.argv[1:]`) and return its exit code.

    Ctrl-C and a closed pipe end the program as they do for the standalone binary: Python's own handlers
    only run when the interpreter regains control, which it does not while `tail -f` or `serve` is running.
    """
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    return _native.cli_main(["logquill", *(sys.argv[1:] if argv is None else argv)])


__all__ = ["main"]
