"""The `logquill` command-line entry point (`pip install logquill` → `logquill tail ...`)."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import time
from pathlib import Path
from typing import IO, Any, Callable, Sequence

from logquill.cli.trace_tree import build_trace, render_tree
from logquill.cli.webui import JSONLSource, RecordSource, SQLiteSource, TraceViewerServer
from logquill.core.levels import Level, parse_level
from logquill.formatters import format_text
from logquill.plugins.tamper_evident_plugin import verify_chain_detailed, verify_signed_chain

_COLORS = {
    Level.TRACE: "\x1b[90m",
    Level.DEBUG: "\x1b[36m",
    Level.INFO: "\x1b[32m",
    Level.WARN: "\x1b[33m",
    Level.ERROR: "\x1b[31m",
    Level.FATAL: "\x1b[35m",
}
_RESET = "\x1b[0m"


def build_parser() -> argparse.ArgumentParser:
    """Builds the `logquill` CLI's argument parser (currently just the
    `tail` subcommand); split out from `main()` so tests can inspect/exercise
    it without going through `sys.argv`."""
    parser = argparse.ArgumentParser(
        prog="logquill", description="LogQuill command-line tools for local development."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    tail_parser = subparsers.add_parser(
        "tail", help="Print (and optionally follow) a LogQuill JSONL log file."
    )
    tail_parser.add_argument("file", help="Path to a LogQuill JSONL log file.")
    tail_parser.add_argument(
        "--level",
        default=None,
        help="Only show records at or above this level (e.g. --level=error).",
    )
    tail_parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON lines instead of human-readable text.",
    )
    tail_parser.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="Keep watching the file and print new records as they're appended.",
    )
    tail_parser.add_argument(
        "-n",
        "--lines",
        type=int,
        default=None,
        metavar="N",
        help="Only show the last N matching records instead of the whole file.",
    )
    tail_parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colorization, even when writing to a terminal.",
    )

    trace_parser = subparsers.add_parser(
        "trace",
        help="Print one agent run's span tree, reconstructed from a JSONL log file.",
    )
    trace_parser.add_argument("run_id", help="The run_id (meta.run_id) to reconstruct.")
    trace_parser.add_argument(
        "--file", required=True, metavar="PATH", help="Path to a LogQuill JSONL log file."
    )
    trace_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the tree as JSON (nested {record, rollup, children}) instead of text.",
    )
    trace_parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colorization, even when writing to a terminal.",
    )

    serve_parser = subparsers.add_parser(
        "serve", help="A local, offline web UI for browsing traced runs."
    )
    source_group = serve_parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--file", metavar="PATH", help="A LogQuill JSONL log file.")
    source_group.add_argument(
        "--db", metavar="PATH", help="A SQLite database a SQLiteTransport wrote."
    )
    serve_parser.add_argument(
        "--table", default="logs", help="Table name, with --db (default: logs)."
    )
    serve_parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1).")
    serve_parser.add_argument(
        "--port", type=int, default=0, help="Bind port (default: 0, let the OS pick one)."
    )

    dev_parser = subparsers.add_parser(
        "dev",
        help="Follow a JSONL log file, live-rendering the current run's span tree as it grows.",
    )
    dev_parser.add_argument("file", help="Path to a LogQuill JSONL log file.")
    dev_parser.add_argument(
        "--run-id",
        default=None,
        help="Track this run_id only; default is whichever run's records arrived most recently.",
    )
    dev_parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colorization and screen-clearing, even on a terminal.",
    )
    dev_parser.add_argument(
        "--backlog",
        type=int,
        default=5000,
        metavar="N",
        help="At most this many existing lines seed the initial view (default: 5000).",
    )

    verify_parser = subparsers.add_parser(
        "verify",
        help="Check a TamperEvidentPlugin-written JSONL log file's hash chain.",
    )
    verify_parser.add_argument("file", help="Path to a LogQuill JSONL log file.")
    verify_parser.add_argument(
        "--sign-key",
        metavar="HEX",
        default=None,
        help="Hex-encoded key, with --signature: also check the chain's head against a "
        "signature taken with TamperEvidentPlugin.sign_head/AuditLogger.sign_head.",
    )
    verify_parser.add_argument(
        "--signature",
        metavar="HEX",
        default=None,
        help="Hex-encoded signature to check against, with --sign-key.",
    )
    return parser


def _passes_filter(record: dict[str, Any], min_level: Level | None) -> bool:
    if min_level is None:
        return True
    try:
        return parse_level(record.get("level")) >= min_level  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _parse_line(line: str, *, warn_stream: IO[str]) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        warn_stream.write(f"logquill tail: skipping malformed JSON line: {line[:200]!r}\n")
        return None
    if not isinstance(parsed, dict):
        warn_stream.write(f"logquill tail: skipping non-object JSON line: {line[:200]!r}\n")
        return None
    return parsed


def _format_human(record: dict[str, Any], *, colorize: bool) -> str:
    line = format_text(record)
    if colorize:
        try:
            color = _COLORS.get(parse_level(str(record.get("level", "?"))))
        except ValueError:
            color = None
        if color:
            line = f"{color}{line}{_RESET}"
    return line


def _emit(record: dict[str, Any], *, as_json: bool, colorize: bool, out: IO[str]) -> None:
    if as_json:
        out.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
    else:
        out.write(_format_human(record, colorize=colorize) + "\n")
    out.flush()


def _read_existing(
    path: Path,
    *,
    min_level: Level | None,
    lines: int | None,
    as_json: bool,
    colorize: bool,
    out: IO[str],
    warn_stream: IO[str],
) -> int:
    """Prints every matching record currently in the file; returns the byte offset at EOF."""
    matched: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            record = _parse_line(raw_line, warn_stream=warn_stream)
            if record is not None and _passes_filter(record, min_level):
                matched.append(record)
        offset = f.tell()

    if lines is not None:
        matched = matched[-lines:]
    for record in matched:
        _emit(record, as_json=as_json, colorize=colorize, out=out)
    return offset


def _follow(
    path: Path,
    offset: int,
    *,
    min_level: Level | None,
    as_json: bool,
    colorize: bool,
    out: IO[str],
    warn_stream: IO[str],
    poll_interval: float = 0.5,
    max_iterations: int | None = None,
) -> None:
    """Polls `path` for lines appended after `offset`, forever unless `max_iterations` is set.

    A polling loop rather than an inotify/kqueue watch: it keeps this module
    dependency-free and behaves the same across platforms, at the cost of up to
    `poll_interval` seconds of latency on a new line — an acceptable trade for a
    local dev tool.
    """
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        try:
            with path.open("r", encoding="utf-8") as f:
                f.seek(offset)
                new_lines = f.readlines()
                offset = f.tell()
        except FileNotFoundError:
            time.sleep(poll_interval)
            continue

        for raw_line in new_lines:
            record = _parse_line(raw_line, warn_stream=warn_stream)
            if record is not None and _passes_filter(record, min_level):
                _emit(record, as_json=as_json, colorize=colorize, out=out)

        time.sleep(poll_interval)


def _run_tail(
    args: argparse.Namespace,
    *,
    min_level: Level | None,
    out: IO[str],
    warn_stream: IO[str],
) -> int:
    path = Path(args.file)
    if not path.exists():
        warn_stream.write(f"logquill tail: no such file: {args.file}\n")
        return 1

    colorize = not args.no_color and not args.json and getattr(out, "isatty", lambda: False)()
    offset = _read_existing(
        path,
        min_level=min_level,
        lines=args.lines,
        as_json=args.json,
        colorize=colorize,
        out=out,
        warn_stream=warn_stream,
    )

    if args.follow:
        with contextlib.suppress(KeyboardInterrupt):
            _follow(
                path,
                offset,
                min_level=min_level,
                as_json=args.json,
                colorize=colorize,
                out=out,
                warn_stream=warn_stream,
            )
    return 0


def _iter_records(path: Path, *, warn_stream: IO[str]) -> Any:
    """Yields every parseable JSON-object line in `path`, one at a time —
    never loads the file into memory, so tracing one run out of a
    multi-gigabyte file costs memory proportional to that run, not the file.
    """
    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            record = _parse_line(raw_line, warn_stream=warn_stream)
            if record is not None:
                yield record


def _run_trace(args: argparse.Namespace, *, out: IO[str], warn_stream: IO[str]) -> int:
    path = Path(args.file)
    if not path.exists():
        warn_stream.write(f"logquill trace: no such file: {args.file}\n")
        return 1

    builder = build_trace(_iter_records(path, warn_stream=warn_stream), args.run_id)
    if builder.record_count == 0:
        warn_stream.write(
            f"logquill trace: no records with meta.run_id={args.run_id!r} found in "
            f"{args.file} — check the run id, or that this file's records carry "
            "run_id at all (see RunPlugin)\n"
        )
        return 1

    if args.json:
        out.write(json.dumps([root.to_dict() for root in builder.roots], default=str) + "\n")
        return 0

    out.write(render_tree(builder.roots, colorize=_tree_colorizer(out, args.no_color)) + "\n")
    return 0


def _tree_colorizer(out: IO[str], no_color: bool) -> Callable[[str, str], str] | None:
    """The `colorize` function `render_tree` wants — level-to-color, matching
    `ConsoleTransport` — or `None` when colorizing doesn't make sense (piped
    output, or explicitly disabled)."""
    if no_color or not getattr(out, "isatty", lambda: False)():
        return None

    def colorize(text: str, level: str) -> str:
        try:
            color = _COLORS.get(parse_level(level))
        except (TypeError, ValueError):
            color = None
        return f"{color}{text}{_RESET}" if color else text

    return colorize


def _run_serve(args: argparse.Namespace, *, out: IO[str]) -> int:
    path = Path(args.file or args.db)
    if not path.exists():
        out.write(f"logquill serve: no such file: {path}\n")
        return 1

    # Deliberately not a ternary (`x if c else y`) despite what a linter
    # suggests: older mypy (the pre-commit hook pins 1.11.2) infers a
    # conditional expression between these two unrelated concrete classes as
    # `object` rather than their common `RecordSource` Protocol, even with an
    # explicit annotation — this if/else form types cleanly on every mypy
    # version instead.
    source: RecordSource
    if args.db:  # noqa: SIM108
        source = SQLiteSource(path, table=args.table)
    else:
        source = JSONLSource(path)
    server = TraceViewerServer(source, host=args.host, port=args.port)
    out.write(f"logquill serve: {len(server.runs)} run(s) found in {path}\n")
    out.write(f"logquill serve: listening on {server.url} — Ctrl+C to stop\n")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


_CLEAR_SCREEN = "\x1b[2J\x1b[H"


def _latest_run_id(records: list[dict[str, Any]]) -> str | None:
    for record in reversed(records):
        run_id = (record.get("meta") or {}).get("run_id")
        if isinstance(run_id, str) and run_id:
            return run_id
    return None


def _run_dev(
    args: argparse.Namespace,
    *,
    out: IO[str],
    warn_stream: IO[str],
    poll_interval: float = 0.5,
    max_iterations: int | None = None,
) -> int:
    """Backs `logquill dev`: follows `args.file`, redrawing the tracked run's
    span tree — cleared and redrawn from scratch each time, like `top` —
    whenever a new record for it arrives. Tracks `args.run_id` if given,
    otherwise whichever run's records have arrived most recently, so the
    view follows an agent from one run to the next without restarting.

    Only the last `args.backlog` lines of whatever the file already holds
    seed the initial view — this isn't the bounded-memory streaming `trace`
    promises for a huge historical file, since a live dev session instead
    keeps growing its own in-memory record list for as long as it runs;
    `--backlog` just keeps a large *pre-existing* file from being read in
    full before the first redraw.
    """
    path = Path(args.file)
    if not path.exists():
        warn_stream.write(f"logquill dev: no such file: {args.file}\n")
        return 1

    colorize = _tree_colorizer(out, args.no_color)
    records: list[dict[str, Any]] = []

    def redraw() -> None:
        run_id = args.run_id or _latest_run_id(records)
        if run_id is None:
            return
        builder = build_trace(records, run_id)
        if colorize is not None:
            out.write(_CLEAR_SCREEN)
        out.write(f"logquill dev — run {run_id}\n\n")
        out.write(render_tree(builder.roots, colorize=colorize) + "\n")
        out.flush()

    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            record = _parse_line(raw_line, warn_stream=warn_stream)
            if record is not None:
                records.append(record)
        offset = f.tell()
    records = records[-args.backlog :]
    redraw()

    iterations = 0
    with contextlib.suppress(KeyboardInterrupt):
        while max_iterations is None or iterations < max_iterations:
            iterations += 1
            try:
                with path.open("r", encoding="utf-8") as f:
                    f.seek(offset)
                    new_lines = f.readlines()
                    offset = f.tell()
            except FileNotFoundError:
                time.sleep(poll_interval)
                continue

            new_records = [
                record
                for record in (
                    _parse_line(raw_line, warn_stream=warn_stream) for raw_line in new_lines
                )
                if record is not None
            ]
            if new_records:
                records.extend(new_records)
                redraw()
            time.sleep(poll_interval)
    return 0


def _run_verify(args: argparse.Namespace, *, out: IO[str]) -> int:
    path = Path(args.file)
    if not path.exists():
        out.write(f"logquill verify: no such file: {args.file}\n")
        return 1

    if bool(args.sign_key) != bool(args.signature):
        out.write("logquill verify: --sign-key and --signature must be given together\n")
        return 1

    records = _iter_records(path, warn_stream=io.StringIO())
    if args.sign_key:
        try:
            key = bytes.fromhex(args.sign_key)
        except ValueError:
            out.write("logquill verify: --sign-key must be hex-encoded\n")
            return 1
        result = verify_signed_chain(records, key=key, signature=args.signature)
    else:
        result = verify_chain_detailed(records)

    if result.ok:
        out.write(
            f"logquill verify: OK — {result.records_checked} record(s) verified in {args.file}\n"
        )
        if result.head_hash is not None:
            out.write(f"logquill verify: chain head is {result.head_hash}\n")
        return 0

    out.write(
        f"logquill verify: FAILED — {result.reason} (at record {result.broken_at} of "
        f"{result.records_checked} checked) in {args.file}\n"
    )
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """The `logquill` console-script entry point: parses `argv` (defaulting
    to `sys.argv`) and dispatches to the matching subcommand, returning the
    process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "trace":
        return _run_trace(args, out=sys.stdout, warn_stream=sys.stderr)

    if args.command == "serve":
        return _run_serve(args, out=sys.stdout)

    if args.command == "dev":
        return _run_dev(args, out=sys.stdout, warn_stream=sys.stderr)

    if args.command == "verify":
        return _run_verify(args, out=sys.stdout)

    if args.command != "tail":
        parser.error(f"Unknown command: {args.command}")

    try:
        min_level = parse_level(args.level) if args.level is not None else None
    except ValueError as exc:
        parser.error(str(exc))
        return 2  # pragma: no cover - argparse.error() already exits

    return _run_tail(args, min_level=min_level, out=sys.stdout, warn_stream=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
