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

The design is in `docs/architecture.md`. Each binding has its build notes in its folder.

## Before you change behaviour

Behaviour shared by every language is defined by `docs/contract.md` and the fixtures in
`schema/`. Change them together with the code, and if a change touches the contract, say so
in the pull request: it means a major version for every package (`docs/versioning.md`).

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
