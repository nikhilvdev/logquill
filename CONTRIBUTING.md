# Contributing

Thanks for considering a contribution to LogQuill. This project follows a
[Code of Conduct](CODE_OF_CONDUCT.md); taking part in issues, PRs and discussions means
agreeing to it.

## Setup

Install Rust with [rustup](https://rustup.rs); `rust-toolchain.toml` selects the right
toolchain and components. Then:

```bash
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

Bindings and extra crates arrive with their stage (`docs/rust-core-plan.md`); each brings its
own build notes in its folder.

## Before you change behaviour

The behaviour of LogQuill is specified in `docs/spec/`. Change the spec and the code
together, and if a change touches a decision in `docs/spec/00-decisions.md` or the parity
rule in `docs/spec/09-parity-and-gap-closing.md`, say so in the pull request.

## Pull request strategy

- **Branch from `develop`** and name branches by intent: `feat/…`, `fix/…`, `docs/…`,
  `chore/…`.
- **Keep a PR to one concern.** A refactor mixed with a feature is harder to review and
  to revert.
- **Definition of done:**
  1. `cargo fmt`, `cargo clippy -D warnings` and `cargo test` pass
  2. New behaviour has a test, and shared behaviour has a fixture under `schema/`
  3. Every binding that exposes the behaviour passes the shared fixtures
  4. `CHANGELOG.md` has an entry under `Unreleased`
  5. Public API additions are documented with an example that was actually run
- **CI must be green** and **at least one review approval** is required before merge.
- **Squash-merge**, with a commit message that summarises the change in the imperative
  mood ("Add rotating file transport", not "Added").

## Reporting issues

Bug reports and feature requests are welcome via GitHub issues. For a security problem,
use the private reporting described in `.github/SECURITY.md`.
