# Stage 4 — CLI and viewer

Goal: `tail`, `trace`, `dev`, `serve` and `verify` in the `logquill` binary, with the viewer page
embedded. Behaviour is in `docs/spec/06-cli-and-viewer.md`; the crate is `crates/logquill-cli`.

## Status: done

| Piece | Where | Checked by |
|---|---|---|
| Streaming JSONL reader that survives non-UTF-8 lines; warnings for malformed and non-object lines | `lines.rs` | `tests/commands.rs` |
| `tail`: level filter, `--json`, `-n` with a bounded window, `-f` following across rotation and truncation, partial lines held | `tail.rs`, `follow.rs` | `tests/commands.rs` |
| `trace`: one run's tree, text or `--json`, coloured on a terminal | `trace.rs`, core `build_trace` / `render_tree_with` | `tests/commands.rs`, core corpus |
| `dev`: live redraw, latest run followed unless `--run-id`, `--backlog` | `dev.rs` | `tests/commands.rs` |
| `serve`: `/`, `/api/runs`, `/api/trace`, `/api/search`; JSONL and SQLite sources; `std::net` only | `serve.rs`, `source.rs`, `runs.rs`, `assets/trace_viewer.html` | `tests/commands.rs` |
| `verify` now reads lines the same way as the rest | `main.rs` | `tests/verify.rs` |

Exit criteria:

- Bounded memory: `logquill-runtime-checks/tests/memory.rs` generates a log and asserts that `trace`, `tail`
  (with and without `-n`) and `serve`'s run summary grow live memory by at most 2 MiB (4 MiB for the summary),
  whatever the file size. It runs at 32 MiB in CI; `LOGQUILL_BIG_MB=1024` runs the full ~1 GB case (13.8 s in a
  release build, passing).
- `tail -f` follows across rotation: `tail_follow_prints_appended_records_and_survives_rotation`.
- Matches the old CLI: `scripts/compare_old_cli.py OLD NEW` runs the published `logquill==2.0.0` command and the
  Rust binary on the same fixture files (including a hash-chained log written by the old library, then edited,
  shortened and truncated) and compares stdout, stderr and exit code. All 19 cases match: `tail` (text, level
  filter, `--json`, `-n`, missing file), `trace` (tree, orphan span, `--json`, unknown run, missing file) and
  `verify` (intact, edited, removed, truncated, bad arguments, no chain). Not compared: `dev` (it needs a terminal)
  and `serve` (its API is checked in `tests/commands.rs`).

Differences from the old CLI, all deliberate:

- Non-ASCII text is written as UTF-8, not `\uXXXX`, in `--json` output and in the `meta` of a text line (decision
  D32). The comparison script decodes the escapes and compares JSON as values.
- `trace --json` has no spaces after `,` and `:` (the old one used Python's defaults). Same values.
- `trace` reports malformed lines as `logquill trace: skipping …`; the old code said `logquill tail:` there by
  copy-paste.

## Changes outside the CLI

- Core `TreeNode` keeps its `record`, and has `to_node_value()` (`{record, rollup, children}`, what the viewer
  reads). `to_value()` is unchanged, so the corpus still passes. `render_tree_with` takes a painter for colour.

## Decisions and limits

- `serve` answers only requests whose `Host` is localhost when bound to loopback, which stops a web page reading
  the logs through DNS rebinding. A non-loopback `--host` has no authentication, as the spec says.
- `tail -f` detects rotation by inode on Unix and by a shrinking file elsewhere. Lines written to the old file
  between the last poll and the rename are not read.
- `serve` starts a thread per connection with a 5 s read timeout and no connection cap.
- A single JSONL line is read whole, so one enormous line is held in memory.
- `dev` keeps every record that has a run id for the session (the tracked run can switch).
