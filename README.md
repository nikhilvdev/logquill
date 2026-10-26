# LogQuill

Structured logging for services and AI agents: one Rust core, one record shape, and an
SDK for each language, so a team running several languages gets one mental model and one
JSON log format.

**Status: 3.0.0 is in development on the `develop` branch. Nothing from this line is
released yet.** The current releases are `logquill` 2.x for Python (PyPI, `main` branch)
and `logquill` 1.x for Node (the separate `logquill-js` repository).

## Layout

| Path | What it is |
|---|---|
| `crates/logquill-core` | Pure logic: records, levels, privacy, integrity. No I/O, no threads. |
| `crates/logquill-cli` | The `logquill` command (starts as a version print; subcommands arrive in later stages). |
| `schema/` | The record schema and the fixtures every language must pass. |
| `docs/` | The plan, the behaviour specification, and reference copies of the 2.x / 1.x docs. |

The runtime crate, the C ABI crate and one folder per language binding are planned
(`docs/rust-core-plan.md`, section 3) and are added when their stage starts, so the
repository stays small and always builds.

## Build and test

```bash
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

## Where to start

- `docs/rust-core-plan.md` — architecture, stages and publishing plan.
- `docs/spec/README.md` — what the 2.x Python and 1.x JS code does, exactly, and
  `docs/spec/00-decisions.md` for the open choices.
- `docs/publishing.md` — how a release reaches every registry, language by language.
- `CONTRIBUTING.md` — how to build and what a change must include.

## Licence

See `LICENSE`. The licence for the 3.0 line is still being decided.
