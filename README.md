# LogQuill

Structured logging for services and AI agents: one Rust core, one record shape, and an
SDK for each language, so a team running several languages gets one mental model and one
JSON log format.

**Status: 3.0.0 is feature-complete on the `develop` branch and unreleased. Nothing from
this line is published to any registry, and a first release candidate (3.0.0-rc.3) is prepared but not published.** The current releases are `logquill` 2.x for Python (PyPI, `main` branch)
and `logquill` 1.x for Node (the separate `logquill-js` repository).

## Layout

| Path | What it is |
|---|---|
| `crates/logquill-core` | Pure logic: records, levels, privacy, integrity, processing and the log-call pipeline. No I/O, no threads. |
| `crates/logquill-runtime` | The bounded queue, the worker thread, and the console, file, syslog, SQLite and HTTP-family transports. |
| `crates/logquill-cli` | The `logquill` command: `tail`, `trace`, `dev`, `serve`, `verify`. |
| `crates/logquill-ffi` | The C ABI (`include/logquill.h`) that the Ruby, .NET, Go, Swift and JVM bindings use. |
| `bindings/python` | Python: a compiled `abi3` wheel (PyO3). |
| `bindings/node` | Node and TypeScript: a napi-rs addon, with a WebAssembly fallback. |
| `bindings/wasm` | Browsers and edge runtimes: the core as WebAssembly. |
| `bindings/{ruby,dotnet,go,swift,jvm}` | The medium tier, on the C ABI. Each folder has its own README. |
| `schema/` | The record schema and the fixtures every language must pass. |
| `docs/` | The plan, the stage notes, the behaviour specification, migration guides and the publishing guide. |

## Build and test

```bash
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

The bindings are built and tested from their own folders; each README says how. `docs/stages/stage-N.md` records what each
stage built, how it was checked, and what is left.

## Where to start

- `docs/rust-core-plan.md` — architecture, stages and publishing plan.
- `docs/migrating/python-2-to-3.md` and `docs/migrating/js-1-to-3.md` — upgrading from the current releases.
- `docs/spec/README.md` — what the 2.x Python and 1.x JS code does, exactly, and
  `docs/spec/00-decisions.md` for the choices that were settled.
- `docs/publishing.md` — how a release reaches every registry, language by language.
- `CONTRIBUTING.md` — how to build and what a change must include.

## Licence

The 3.0 line is MIT licensed (see `LICENSE`). Python 2.0.0 stays FSL-1.1-MIT and every earlier release stays MIT, as published.
