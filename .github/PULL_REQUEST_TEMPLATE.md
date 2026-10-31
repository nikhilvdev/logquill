## Summary

<!-- What does this change, and why? -->

## Checklist

- [ ] `cargo fmt --all --check`, `cargo clippy --workspace --all-targets -- -D warnings`
      and `cargo test --workspace` pass
- [ ] New behaviour has a test; shared behaviour has a fixture under `schema/`
- [ ] Every binding that exposes the behaviour passes the shared fixtures
- [ ] The spec in `docs/spec/` is updated if behaviour changed (and a decision in
      `docs/spec/00-decisions.md` if a choice was made)
- [ ] Public API additions are documented with an example that was actually run
- [ ] `CHANGELOG.md` has an entry under `Unreleased`

## Scope

<!-- One concern per PR where possible. Call out here if this
     intentionally spans more than one. -->
