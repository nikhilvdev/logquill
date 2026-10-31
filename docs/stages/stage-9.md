# Stage 9 — docs and 3.0.0

Goal: migration guides, current docs, one changelog, and the release checklist run once, together, for every language. Plan:
`docs/rust-core-plan.md` (Stage 9, and section 7 for the release mechanics). Publishing: `docs/publishing.md`.

## Status: a release candidate (3.0.0-rc.1) is prepared in the working tree; publishing waits on accounts and your go-ahead

Branch `feat/stage-9-docs`, cut from `develop` after Stages 7 and 8 merged (PRs #34, #35). Nothing is committed or pushed.

| Piece | State |
|---|---|
| Python 2.x to 3.0 migration guide | **done**: `docs/migrating/python-2-to-3.md` |
| JS 1.x to 3.0 migration guide | **done**: `docs/migrating/js-1-to-3.md` (the 1.x to 2.0 record changes are summarised there and kept in full in `docs/legacy/js/MIGRATING.md`) |
| One changelog | **done**: `CHANGELOG.md` now covers every stage, with the deliberate differences from 2.0.0 |
| README | **done**: layout, status and entry points describe the whole repository |
| Per-language READMEs | already done in their stages; each example is run by that binding's tests |
| API reference regenerated | **builds, not published**: `python -W error -m pdoc logquill` and `npm run docs` (TypeDoc, warnings as errors) both build with zero warnings; hosting them (GitHub Pages) is not set up |
| Contract-table version bumped everywhere | **not done**, see "Decisions" |
| Release checklist run (version bump, tag, publish) | **not started**, see "Decisions" |

## What the guides rest on

Every behaviour in the Python guide is a numbered decision (D2, D3, D9, D20, D21, D22, D27, D32) listed in
`docs/stages/stage-5.md` and checked by the oracle tests against the published 2.0.0. The JS guide rests on
`docs/stages/stage-6.md` and the legacy 1.x to 2.0 guide. I did **not** run a 1.x or 2.x application through a real upgrade;
the checklists in the guides are derived from the settled differences, not from an observed migration.

## Decisions that block the release

1. ~~**Licence.**~~ **Settled: MIT** for the 3.0 line (your decision). Root `LICENSE` is now the MIT text (the FSL-1.1-MIT text it
   replaced is in git history and stays the licence of Python 2.0.0); every package has a licence field and a `LICENSE` copy. The
   text has had no legal review.
2. ~~**Whether `schema_version` changes.**~~ **Settled: it stays `"2.0"`.** The 3.0 line is a new implementation of the same
   contract (`crates/logquill-core/src/lib.rs`), so the table and `schema/` do not change and only the package versions move.
3. **Names and namespaces.** The Maven group ID `io.logquill` is not claimed; the Go module path
   (`github.com/nikhilvdev/logquill/bindings/go/v3`) assumes the repository stays where it is; the Swift mirror repository does
   not exist. Each registry's first release also needs its own account, trusted publisher and a GitHub environment with
   reviewers (`docs/publishing.md`).

## Release preparation (done in the working tree, uncommitted)

- **Licence: MIT.** Root `LICENSE` is the MIT text; every package has a licence field (`license = "MIT"` in Cargo and
  `pyproject.toml`, `"license"` in `package.json`, `spec.license` in the gemspec, `PackageLicenseExpression` in the `.csproj`, a
  POM `licenses` block for Gradle) and a `LICENSE` copy in its directory (Go and Swift take it from the module or package root).
  The Python wheel's metadata reads `License-Expression: MIT`.
- **Version: `3.0.0-rc.1`** in all ten manifests, spelled `3.0.0rc1` for PyPI and `3.0.0.rc1` for RubyGems; `check-versions.py`
  passes with and without the tag `v3.0.0-rc.1`. The generated `bindings/node/index.js` and the lock files carry it too.
- **Publishable manifests.** The four crates no longer have `publish = false` (`logquill-runtime-checks` keeps it) and their
  path dependencies carry a version. The two npm packages are no longer `private`, with `provenance: true`.
  `cargo publish --dry-run -p logquill-core` passes; the other three crates cannot be dry-run until core is on crates.io.
- **`.github/workflows/release.yml`.** On a pushed `v*` tag: the tag must equal every manifest, the whole CI runs on the tagged
  commit (`ci.yml` has `workflow_call`), then each registry publishes from its own GitHub environment. Every action is pinned to a
  commit SHA and nothing uses a stored password: crates.io, PyPI, npm, RubyGems and NuGet use trusted publishing (OIDC).

  | Registry | What the workflow builds and publishes | Environment |
  |---|---|---|
  | crates.io | `logquill-core`, `-runtime`, `-ffi`, `-cli`, in that order | `crates-io` |
  | PyPI | wheels for Linux x86_64, aarch64 and musl, macOS arm64 and x86_64, Windows, plus the source distribution | `pypi` |
  | npm | `@logquill/wasm`; `logquill` plus one `@logquill/native-*` package per platform (`linux-x64-gnu`, `linux-arm64-gnu`, `darwin-arm64`, `darwin-x64`, `win32-x64-msvc`) | `npm` |
  | RubyGems | one platform gem per native library (five) | `rubygems` |
  | NuGet | one package holding all five runtimes | `nuget` |
  | Maven Central | a GPG-signed bundle (jar with five natives, sources, Javadoc, POM, checksums), **uploaded for a manual release in the portal** (`USER_MANAGED`), never published automatically | `maven-central` |
  | Go | tags `bindings/go/<version>` after checking `core.wasm` is tracked and is this version's core | none (`contents: write`) |
  | Swift | the five-slice XCFramework (macOS, iOS device, iOS simulator), its checksum, and a commit, tag and release in the mirror repository | `swift-mirror` |

  A release candidate (`-rc.N`) goes to npm's `next` tag and to GitHub as a pre-release. **The workflow has never run**: it only
  runs on a tag, and I cannot push one. Its building blocks were run here: `check-versions.py`; the platform-gem, NuGet and Maven
  bundle scripts (each with the one macOS library I have, including a real GPG signature verified with a throwaway key); the Swift
  zip, checksum and mirror generator (host slice only: the iOS slices need Xcode); `napi create-npm-dirs` and the link script;
  and `go test` of the core-version check. Not run: a multi-platform build, any upload, OIDC login, the iOS slices.

- **Go.** `bindings/go/core.wasm` (1.6 MB) is no longer ignored: it is committed so a proxy fetch of the tag has a core to embed.
  `go test` fails if its version differs from the workspace's (`TestTheEmbeddedCoreIsTheWorkspaceVersion`, via the new
  `logquill.CoreVersion()`), so a stale file cannot be tagged. Each rebuild adds 1.6 MB to the repository's history.
- **Linux libraries** are built on Ubuntu 22.04 (glibc 2.35), so they will not load on older distributions; musl (Alpine) has no
  native library for Ruby, .NET, the JVM or the Node addon (the Node musl target was removed). Python has a musllinux wheel.

## What only you can do

The step-by-step version, with the exact field values for every registry, the `gh` commands for the environments, and what to do
when a name does not exist yet, is `docs/release-setup.md`. In outline:

1. **Create the GitHub environments** `crates-io`, `pypi`, `npm`, `rubygems`, `nuget`, `maven-central` and `swift-mirror`, with
   required reviewers, and register a **trusted publisher** on crates.io, PyPI (project `logquill`, which already has the 2.x releases
   per the project notes), npm (`@logquill/wasm`, `logquill` and `@logquill/native-*`), RubyGems and NuGet, each pointing at
   repository `nikhilvdev/logquill`, workflow `release.yml` and the environment above.
2. **Set the repository variable** `NUGET_USER` (the nuget.org account that owns `LogQuill`).
3. **Maven Central.** Create a Central Portal account and a **verified namespace**, a GPG key (publish its public half to a
   keyserver), and the environment secrets `GPG_PRIVATE_KEY` (armored), `GPG_PASSPHRASE` and `CENTRAL_PORTAL_TOKEN`. The group ID in
   `bindings/jvm/build.gradle.kts` is `io.logquill`; Central only lets you publish under a namespace you verified, and
   `io.logquill` needs you to own the domain `logquill.io` (DNS TXT record). **If you do not own it, the group must change to
   `io.github.nikhilvdev`** (verified through GitHub) in `build.gradle.kts`, `central-bundle.sh`, `pack-smoke.sh`, the JVM README
   and the workflow's namespace URL; the Java package `io.logquill` can stay.
4. **Swift.** Create the public repository `nikhilvdev/logquill-swift` (empty is fine) and the environment secret
   `SWIFT_MIRROR_TOKEN`, a fine-grained token with contents write on that repository only.
5. Review, commit and push this tree and get CI green on it.
6. Push the tag `v3.0.0-rc.1` when you are ready. The first run will probably need small fixes; an rc tag is the safe way to
   find them, because npm sends it to `next`, GitHub marks it a pre-release, the Maven bundle waits in the portal, and a
   version a registry has accepted can be superseded but not changed.
7. For the final `3.0.0`: bump the ten manifests (no bump script exists), move the changelog's `Unreleased` section under the
   dated heading in the same commit, rebuild `core.wasm`, and tag `v3.0.0`.

## Fixed in the follow-up pass

- `develop`'s only red job: `LogfmtFormatter` threw on an invalid `Date` in `meta` (found by a property test; fixed, with a
  regression test).
- `sampling`, `rate_limit` and `adaptive_sampling` helpers in the Ruby, .NET, Go, Swift and JVM wrappers, with a test each and
  README lines; the Go `core.wasm` and the Swift XCFramework had to be rebuilt to carry slice 8.
- The Node and WASM glue now use the core's `parse_lenient` (two copies of the function removed); the WASM tarball ships the
  module once (`pack-smoke` enforces it).
- Go's file transport can rotate by size (`NewRotatingFileTransport`).
- `scripts/check-versions.py` (and a CI job) checks that all ten manifests, and a release tag, carry one version.
- A duplicate `FieldClass` type in the Node SDK; `npm run docs` (TypeDoc) and pdoc both build with zero warnings.
- JVM per-call cost measured (`gradle bench`).

## Known gaps carried into the release

These are written down in the stage documents and stay open:

- Python latency gate: the queued-hand-off row is unstable on the shared Ubuntu runner (it failed at 2.16x and 1.29x; it is
  0.85x to 0.97x of 2.0.0 on the build machine). The gate now takes the median of five runs and allows that one row 1.5x; the
  synchronous and filtered rows keep 1.10x. Whether a Linux machine really is slower on that row is still unmeasured.
- Stage 7 and 8 bindings have never run on Windows (not in the CI matrix: the scripts are `sh` and the paths assume Unix, so a
  Windows job would need real porting work, not a matrix line); .NET 8 is compile-checked only; Swift's checks are still an
  executable, not an XCTest target.
- Tail sampling, the flight recorder, the run summary and alert de-duplication are not in the C ABI, so not in the
  medium-tier bindings. They release *earlier* records or emit new ones on a timer, so the logger and `lq_log` need a second
  output path (a batch of records per call), which changes the ABI and all five wrappers; that is a design of its own.
- Not started: Kotlin extensions, and bridges for `log/slog`, SLF4J, `swift-log` and `Microsoft.Extensions.Logging`.
- Firefox and Safari are untested for the browser build.
