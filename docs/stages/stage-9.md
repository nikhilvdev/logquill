# Stage 9 — docs and 3.0.0

Goal: migration guides, current docs, one changelog, and the release checklist run once, together, for every language. Plan:
`docs/rust-core-plan.md` (Stage 9, and section 7 for the release mechanics). Publishing: `docs/publishing.md`.

## Status: documentation done; the release itself waits on decisions that are yours

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

1. **Licence.** The core under FSL-1.1-MIT (current Python 2.x) or MIT (JS and all 1.x). Every manifest has no licence field and
   no `LICENSE` file ships in a package until this is settled (plan, section 8). No registry accepts a first publish cleanly
   without it.
2. **Whether `schema_version` changes.** The record contract is `"2.0"` and the 3.0 line writes `"2.0"`
   (`crates/logquill-core/src/lib.rs`). The plan says the contract-table version is "bumped everywhere together"; if 3.0 is only
   the implementation (the shape is unchanged), the table and `schema/` stay at 2.0 and only the package versions move. A change
   to the table is a major bump in both repos.
3. **Names and namespaces.** The Maven group ID `io.logquill` is not claimed; the Go module path
   (`github.com/nikhilvdev/logquill/bindings/go/v3`) assumes the repository stays where it is; the Swift mirror repository does
   not exist. Each registry's first release also needs its own account, trusted publisher and a GitHub environment with
   reviewers (`docs/publishing.md`).

## What the release checklist still needs (none of it done)

- Bump every manifest from `3.0.0-dev.0` (`3.0.0.dev0` for Python and Ruby) to `3.0.0`, in one commit, with the changelog
  heading dated.
- Commit the Go `core.wasm` before tagging; build the multi-platform XCFramework, zip it and write its checksum into the Swift
  mirror; build a Python wheel and Node platform package per target (only the build machine's were ever exercised).
- Create the release workflow described in plan section 7 (`release.yml`): tag equals every manifest, build matrix, tests on the
  tagged commit, then one publish job per registry behind a reviewed environment.
- CI green on the exact tagged commit. The `develop` run for PR #35 was still pending when this stage started.
- Cross-repo sync: the `logquill-js` repository's contract table and a tracking issue, if the table changes.
- Rebuild the API references (pdoc, typedoc) with zero warnings.

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
