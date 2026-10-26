# 06 — CLI and trace viewer (Python only today)

Entry point `logquill` with subcommands `tail`, `trace`, `serve`, `dev`, `verify`. No
subcommand → usage error. Messages are prefixed `logquill <command>:`.

## `tail FILE [--level L] [--json] [-f|--follow] [-n|--lines N] [--no-color]`

- File missing → `logquill tail: no such file: <path>`, exit 1. A bad `--level` is a usage
  error from the level parser.
- Read every line; blank lines skipped; invalid JSON → warning on stderr
  `logquill tail: skipping malformed JSON line: <first 200 chars, quoted>`; valid JSON that
  is not an object → `…skipping non-object JSON line: …`.
- Keep records at or above `--level` (a record whose level is missing or invalid is
  dropped when filtering).
- `-n N` shows only the last N matching records. **Defect:** the current code holds every
  matching record in memory to do this; use a bounded window and print as it reads.
- Output: with `--json`, the compact JSON of each record; otherwise the text line (see 04),
  coloured by level (same codes as the console transport) only when not `--no-color`, not
  `--json` and stdout is a terminal. Flush after each record.
- `-f`: after printing, poll the file every 0.5 s from the saved byte offset and print new
  complete records; a missing file is waited for; Ctrl-C ends quietly. **Defect:** if the
  file shrinks (rotation) the offset must reset to 0, and a partial last line must be held
  until its newline arrives.

## `trace RUN_ID --file PATH [--json] [--no-color]`

- Missing file → `logquill trace: no such file: <path>`, exit 1.
- Stream the file line by line (same line parsing, but malformed-line warnings go to
  stderr), feed each record to the trace builder; only records of this run are kept.
- No records → `logquill trace: no records with meta.run_id=<id> found in <path> — check the
  run id, or that this file's records carry run_id at all (see RunPlugin)`, exit 1.
- `--json`: one line, a JSON array of root nodes `{record, rollup:{tokens_in, tokens_out,
  cost_usd}, children:[…]}`. Otherwise the rendered tree plus a newline, coloured by each
  record's level when stdout is a terminal and not `--no-color`.

### Trace builder (streaming, one run)

State: `nodes` (span id → node), `pending` (parent span id → nodes waiting for it),
`roots`, `record_count`. For each record in file order, if `meta.run_id` equals the run:
count it; create a node; if the node has a `meta.span_id` (non-empty string) take
`pending[span_id]` as its children and register it in `nodes`; then if it has a
`meta.parent_span_id` (non-empty string): attach to `nodes[parent]` if known, else add to
`pending[parent]`; with no parent it becomes a root. `finalize()` appends every still-pending
node to `roots` (a parent that never closed). It relies on how spans are written: everything
inside a span is logged before the span's closing record, and a nested span closes before
its parent. **Defect:** a record whose `parent_span_id` equals its own `span_id`, or a loop
of two, never reaches `roots` and disappears; treat a self-parent as no parent and append
unreachable nodes at the end.

Node fields used: `is_span` (`meta.kind == "span"`), `duration_ms` (real number, not bool),
own tokens/cost from `llm` (`tokens_in`, `tokens_out` integers not bool; `cost_usd` real).
Rollup = own + all descendants.

### Tree rendering

One line per node, children in written order:

```
<prefix><connector><label>
connector: "└─ " for the last sibling, "├─ " otherwise
child prefix: parent prefix + "   " after the last sibling, "│  " otherwise
label: "[<LEVEL>] <message>" + annotations
annotations: "  (" + parts joined by ", " + ")" when any part exists
parts, in order:  "<duration with thousands separator, 1 decimal>ms"   if duration_ms
                  "<tokens_in>→<tokens_out> tok"                       if rollup tokens non-zero
                  "$<cost, thousands separator, 4 decimals>"           if rollup cost non-zero
```

An optional colouriser wraps the label by the record's level. Roots are siblings at the
top level.

## `dev FILE [--run-id ID] [--no-color] [--backlog N=5000]`

Missing file → `logquill dev: no such file: <path>`, exit 1. Read the existing file, keep
the last `N` lines' records, draw the tracked run (`--run-id` if given, else the run of the
most recent record that has a `run_id`; nothing is drawn until a run id exists). Redraw
from scratch whenever new records arrive (poll 0.5 s): when colour is on, first write
`\x1b[2J\x1b[H`; then `logquill dev — run <id>` and a blank line, then the tree and a
newline. Records are kept in memory for the session (documented, unlike `trace`).

## `verify FILE [--sign-key HEX --signature HEX]`

Missing file → `logquill verify: no such file: <path>`, exit 1. `--sign-key` and
`--signature` must come together (`logquill verify: --sign-key and --signature must be
given together`, exit 1); a non-hex key → `logquill verify: --sign-key must be hex-encoded`.
Records are streamed (unparseable lines skipped silently). On success: `logquill verify:
OK — <n> record(s) verified in <file>` then, if there is a head, `logquill verify: chain
head is <hash>`; exit 0. On failure: `logquill verify: FAILED — <reason> (at record <k> of
<n> checked) in <file>`, exit 1. Reasons are in 03.

## `serve (--file PATH | --db PATH) [--table logs] [--host 127.0.0.1] [--port 0]`

`--file` and `--db` are mutually exclusive and one is required. Missing path → `logquill
serve: no such file: <path>`, exit 1. On start: summarise runs once by streaming the source
(a snapshot; new records need a restart), print `logquill serve: <n> run(s) found in
<path>` and `logquill serve: listening on <url> — Ctrl+C to stop`. Port 0 lets the OS pick.
A threaded HTTP server; no authentication, so `--host` other than loopback exposes the logs.

Sources: **JSONL** (invalid lines and non-objects skipped); **SQLite** via the transport's
table (`SELECT timestamp, level, logger, message, meta, run_id, span_id, parent_span_id,
trace_id FROM <table> ORDER BY id`): each row becomes a record with `schema_version "1.0"`,
`meta` = the stored JSON (a bad value → `{}`; non-object → `{}`) with `run_id`, `span_id`,
`parent_span_id`, `trace_id` filled in when the column is not null (`setdefault`). The row
carries no `llm`, so tokens and cost are not shown for SQLite sources.

### HTTP API

| Request | Response |
|---|---|
| `GET /` | the viewer page, `text/html; charset=utf-8` |
| `GET /api/runs` | JSON array of run summaries, newest `start` first |
| `GET /api/trace?run_id=ID` | JSON array of root nodes (as in `trace --json`); missing id → 400 `{"error":"run_id is required"}` |
| `GET /api/search?q=&level=&run_id=&limit=` | JSON array of at most `limit` records (default and maximum 500) |
| anything else | 404 |

Run summary: `run_id`, `record_count`, `start` and `end` (min/max timestamp text), `level_counts`
(level → count), `tokens_in`, `tokens_out`, `cost_usd` (sums of `llm`), `agent_name` (the
`meta.agent_name` of a record with `meta.operation == "invoke_agent"`), `error_count`
(ERROR + FATAL). Records without a string `run_id` are not in the run list (they are still
searchable). Search: keep a record when its level equals `level` (if given), its
`meta.run_id` equals `run_id` (if given), and the lower-cased query occurs in
`"<message> <meta JSON>"` lower-cased; stop at `limit`. **Defect:** a non-numeric `limit`
raises, and `limit` ≤ 0 still returns one record; clamp to 1..500 and answer 400 on bad
input. Every request that needs records streams the source again; memory is O(one run) or
O(one page).

### The viewer page (`../legacy/python/trace_viewer.html`)

Single static page, light/dark themes via CSS variables (level colours: trace grey, debug
cyan, info green, warn amber, error red, fatal purple), no external requests. Left
sidebar: search box (message and meta) and a level filter; run list showing run id, an
`<n> err` badge when `error_count > 0`, `agent · records · tokens · cost`, and the start time.
Main area: with no run selected, search across all runs; with a run selected, the span tree
with expand/collapse, a level tag, a waterfall bar (start = record time − `duration_ms`,
scaled to the run's range, minimum width 0.3 %), the message and annotations (`duration`,
`tokens_in→tokens_out tok`, `$cost`), non-matching rows dimmed and rows with no matching
descendant hidden while filtering; clicking a row shows the full record as pretty JSON in a
detail pane. Duration shows as `Xs` from 1000 ms, else `X.Xms`. All text is HTML-escaped.
The page is the asset to embed in the Rust binary unchanged.
