# Contributing to LogQuill

Thanks for considering a contribution to LogQuill. This document is the working agreement for everyone who changes the
repository, whether you are a maintainer, a contributor from another company, or a first-time visitor. It covers how to
set up, what a change must contain, how it is reviewed and merged, and how releases and security reports are handled.

This project follows a [Code of Conduct](CODE_OF_CONDUCT.md); taking part in issues, PRs and discussions means agreeing
to it.

## Contents

- [How the repository is organised](#how-the-repository-is-organised)
- [Ways to contribute](#ways-to-contribute)
- [Development setup](#development-setup)
- [Before you change behaviour](#before-you-change-behaviour)
- [Branches, commits and pull requests](#branches-commits-and-pull-requests)
- [Definition of done](#definition-of-done)
- [Testing](#testing)
- [Code quality bar](#code-quality-bar)
- [Documentation and changelog](#documentation-and-changelog)
- [Review and merge](#review-and-merge)
- [CI](#ci)
- [Dependencies and supply chain](#dependencies-and-supply-chain)
- [Security](#security)
- [Releases](#releases)
- [Licensing of contributions](#licensing-of-contributions)
- [Getting help](#getting-help)

## How the repository is organised

One monorepo, nine languages. The 3.0 line (`develop`) is a Rust core plus one SDK per language.

| Path | What lives there |
|---|---|
| `crates/logquill-core` | Pure logic: records, pipeline, plugins, privacy, integrity. No I/O, no threads |
| `crates/logquill-runtime` | Bounded queue, worker thread, native transports, watchers |
| `crates/logquill-ffi` | The C ABI used by Ruby, .NET, JVM and Swift |
| `crates/logquill-cli` | The `logquill` command (`tail`, `trace`, `dev`, `serve`, `verify`) |
| `crates/logquill-runtime-checks` | Checks that need `unsafe`. Not published |
| `bindings/<language>` | Native glue plus the idiomatic SDK layer (Python, Node, WASM, Go, JVM, .NET, Ruby, Swift) |
| `schema/` | Record schema, golden records and the shared corpus every binding must pass |
| `docs/` | Public documentation. Start at [`docs/README.md`](docs/README.md); the design is [`docs/architecture.md`](docs/architecture.md) |

The 2.x Python releases live on `main` and under the `v2.0.0` tag. Do not target `main` with 3.0 work.

## Ways to contribute

- **Report a bug or request a feature** through [GitHub issues](https://github.com/nikhilvdev/logquill/issues/new/choose)
  using the templates. A bug report needs the language, the package version (`logquill --version` or your lock file)
  and a minimal reproduction.
- **Improve the documentation.** Fixes to `docs/` and the binding READMEs are welcome and need no design discussion.
- **Fix a bug or add a feature.** For anything larger than a small fix, open an issue first and describe the approach, so
  that you do not build something the project cannot accept. Changes to the cross-language contract in particular need
  agreement before code (see the next section).
- **Report a security problem** privately. See [Security](#security). Never open a public issue for one.

## Development setup

Install Rust with [rustup](https://rustup.rs); `rust-toolchain.toml` selects the stable toolchain with `rustfmt` and
`clippy`. Then, from the repository root:

```bash
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

These three commands are the gate for the Rust workspace. Run them before every push.

### Bindings

The Python, Node and WASM bindings sit outside the Cargo workspace. Build and test each from its own folder, as its
README says (`bindings/<language>/README.md`). In short:

| Binding | Prerequisites | Build and test |
|---|---|---|
| Python | Python 3.10+, a virtualenv | `pip install -e ".[dev,http,hooks]"`, `pre-commit install`, then the checks in the README |
| Node | Node 22+ | `npm run build`, `npm run build:sdk`, `npm test` |
| WASM | `wasm32-unknown-unknown` target, Node 22+, the pinned `wasm-bindgen-cli` | See the README; the build script says what to install |
| Go | Go toolchain | `bindings/go/scripts/build-core.sh`, then `go test ./...` |
| JVM | JDK 17+ | `cargo build -p logquill-ffi --release`, then `gradle test` in `bindings/jvm` |
| .NET | .NET 8 or 10 SDK | `cargo build -p logquill-ffi --release`, then `dotnet test bindings/dotnet/tests/LogQuill.Tests` |
| Ruby | Ruby 3.1+ | `cargo build -p logquill-ffi --release`, then the tests in `bindings/ruby` |
| Swift | Xcode, macOS | `bindings/swift/scripts/build-xcframework.sh`, then `swift build` |

If your change touches the Rust core, build and test **every binding that exposes the behaviour**, not only the one you
use day to day.

## Before you change behaviour

Behaviour shared by every language is defined by [`docs/contract.md`](docs/contract.md) and the fixtures in `schema/`.

- **Shared behaviour is changed in the Rust core and the fixtures together, never in one binding alone.** A binding that
  disagrees with the corpus is a bug in the binding.
- **Naming must match [`docs/api-names.md`](docs/api-names.md) exactly.**
- **Changing the contract or `schema/` is a major version for every package** ([`docs/versioning.md`](docs/versioning.md)).
  Say so in the issue and in the pull request description. This includes the record shape, level weights, field
  names, the `llm` block, the content policy and the hash-chain format.
- **The stability promise covers** the record shape, the public names, the `logquill` command and its flags, and the
  on-disk formats. Anything marked experimental in a binding's README is not covered.

## Branches, commits and pull requests

- **Branch from `develop`** and name branches by intent: `feat/…`, `fix/…`, `docs/…`, `chore/…`.
- **Keep a PR to one concern.** A refactor mixed with a feature is harder to review and to revert. If a PR
  intentionally spans more than one concern, say why in its Scope section.
- **Fill in the pull request template.** It carries the checklist reviewers use.
- **Commit messages** summarise the change in the imperative mood ("Add rotating file transport", not "Added"). Commits
  on your branch can be as granular as you like; the PR is squash-merged, so its title becomes the commit on `develop`.
- **Keep the PR current.** Rebase or merge `develop` into your branch when it falls behind, and re-run the checks.
- **Never commit secrets,** key files, `.env` files or credentials. Secret scanning is configured for the repository,
  and a leaked credential must be revoked, not just deleted from history.
- **`docs-internal/` is maintainer-only and gitignored.** Do not commit it and do not link to it from tracked files.

## Definition of done

A change is ready for review when all of these are true:

1. `cargo fmt`, `cargo clippy -D warnings` and `cargo test` pass.
2. New behaviour has a test, and shared behaviour has a fixture under `schema/`.
3. Every binding that exposes the behaviour passes the shared fixtures.
4. Public API additions are documented with an example that was actually run.
5. `CHANGELOG.md` has an entry under `Unreleased`, written as prose a user would read.
6. If the contract changed, `docs/contract.md` is updated and the bump is major.

## Testing

- Every new branch of logic gets a test. A bug fix starts with a test that fails without the fix.
- Cover the edge cases: empty input, a down transport, a plugin that throws, a record with no `meta`.
- Shared behaviour is tested through the corpus in `schema/`, which every binding runs. Add a golden record or corpus
  input rather than duplicating the assertion in each language.
- Code that needs `unsafe` goes in `crates/logquill-runtime-checks`, never in the core, which forbids it
  (`unsafe_code = "forbid"`). The runtime's worker and queue tests run under `miri` in CI.
- Do not make tests depend on the network. Use local sockets or fakes.
- Examples in READMEs and docs are run by the binding test suites; keep them runnable.

## Code quality bar

- Docstrings explain what and why, not just the name.
- Error messages say what happened and what to do about it.
- No `TODO`, `FIXME` or commented-out code on the release path.
- **Logging must never raise into the caller.** A failure inside LogQuill is contained and reported, not thrown at the
  application.
- **Privacy fails closed.** A broken redactor drops the record rather than letting unredacted data through. Prompt and
  completion content stays off by default.
- Keep the core pure: no I/O and no threads in `logquill-core`.
- Follow the style of the surrounding code, including comment density, naming and idiom. Formatters and linters
  (`cargo fmt`, `clippy`, `ruff` for Python) are authoritative.

## Documentation and changelog

- User-facing changes go in `CHANGELOG.md` under `Unreleased`, as a sentence a user would read ("Added a rotating file
  transport that…"), not a commit title.
- Update `docs/` and the relevant binding README when behaviour or public names change.
- A new public API needs a short example, and that example must have been run, not written from memory.
- Documentation must not link to `docs-internal/`.

## Review and merge

- **At least one approving review** is required, and [`.github/CODEOWNERS`](.github/CODEOWNERS) assigns reviewers
  automatically.
- **CI must be green** on the exact commit that merges.
- Reviewers look at correctness, safety, behaviour under load, whether risky code is tested, whether the change
  respects the contract, and whether every line is needed. Expect questions and requests for change; they are about the
  code, not about you.
- Resolve review threads yourself once addressed, or say why you disagree. Do not force-push over a review in progress
  without saying so.
- Merging is a **squash-merge** into `develop`.

## CI

Every pull request runs [`.github/workflows/ci.yml`](.github/workflows/ci.yml): the Rust workspace on several
operating systems, `miri` on the worker and queue, each binding (Python including the free-threaded build, Node, WASM,
the C ABI, Ruby, .NET, Go, Swift, JVM), a check that every manifest carries the same version, and the release packaging
scripts. A separate workflow runs the shared corpus. If a check is flaky, open an issue with the run link instead of
re-running until it passes.

## Dependencies and supply chain

- Prefer the standard library or an existing dependency over a new one. Justify any new dependency in the PR: what it
  does, its licence, how well maintained it is, and why the code cannot be written locally.
- The Python and npm packages carry the compiled core and have no required runtime dependency tree. Do not change that.
- Dependabot opens weekly pull requests for Cargo and GitHub Actions. Review them like any other change.
- Third-party GitHub Actions in the release workflow are pinned to a commit SHA.
- Dependencies must be compatible with the MIT licence.

See [`docs/supply-chain.md`](docs/supply-chain.md) for the full list of controls.

## Security

**Do not open a public issue for a vulnerability.** Use GitHub's private vulnerability reporting, described in
[`.github/SECURITY.md`](.github/SECURITY.md), and include a description, reproduction steps and the affected versions.
Fixes target the latest release of each supported line (3.x, and the latest 2.x Python release).

When contributing code, treat inputs from records, config files, plugins and transports as untrusted, never log secrets
or raw personal data, and do not weaken the redaction, privacy or integrity (hash-chain) paths without a security review
in the PR. The privacy and tamper-evidence features are technical controls, not a compliance guarantee; do not word
documentation as if they were.

## Releases

Releases are done by maintainers and are described in [`docs/releasing.md`](docs/releasing.md). Contributors need to know:

- All packages share one **lockstep version** following [Semantic Versioning](docs/versioning.md).
- A `vX.Y.Z` tag releases every language; `<language>-vX.Y.Z` releases one. **The tag is the only thing that
  publishes.** Nothing is published by hand from a laptop.
- CI must be green on the exact commit before it is tagged, and versions are never skipped.
- Do not bump versions in a feature PR unless the maintainers asked for it.

## Licensing of contributions

LogQuill 3.x is released under the [MIT licence](LICENSE). By submitting a contribution you confirm that you wrote it or
have the right to submit it, and that it may be distributed under that licence. If your employer has rights to your work,
make sure you have permission to contribute before you open the PR.

## Getting help

- Start with the [documentation](docs/README.md) and the [architecture](docs/architecture.md) page.
- Questions, bugs and feature requests: see [`.github/SUPPORT.md`](.github/SUPPORT.md). There is no service-level
  agreement; reports are handled on a best-effort basis.
- Conduct concerns: see the [Code of Conduct](CODE_OF_CONDUCT.md).
