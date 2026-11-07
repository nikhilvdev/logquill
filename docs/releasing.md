# How a release is made

Releases are cut by pushing a git tag. Nothing is published from a developer machine.

## Tags

| Tag | Releases |
|---|---|
| `vX.Y.Z` | Every language at once |
| `<language>-vX.Y.Z` | One language only: `crates-`, `python-`, `npm-`, `ruby-`, `nuget-`, `maven-`, `go-`, `swift-` |

A tag runs only that language's tests and publish jobs. A failing or unconfigured registry does not block the others.

## Steps

1. **Merge to `main` with CI green.** Branches start from `develop`; CI must pass and a review is required.
2. **Finalise the version.** Move the `Unreleased` section of `CHANGELOG.md` under `## X.Y.Z - YYYY-MM-DD` and bump
   the version in the manifests. CI fails if any manifest disagrees (`scripts/check-versions.py`), or if the committed
   Go `core.wasm` does not match the workspace version.
3. **Check CI on the exact commit** you are about to tag.
4. **Tag and push.** The release workflow builds, tests and publishes.
5. **Watch the run to the end** and check each registry shows the new version.
6. **No skipped versions.** If a run fails partway, fix forward on the same version where the registry allows, or
   record in the changelog why a version was abandoned.

## How publishing is authorised

- crates.io, PyPI, npm, RubyGems and NuGet use **trusted publishing** (OpenID Connect from the workflow, a short-lived
  token, no stored secret).
- npm packages are published with `--provenance`.
- Maven Central uses a GPG signing key and a portal token; Swift uses a token for its mirror repository. Both are
  GitHub environment secrets.
- Each registry publishes from its own GitHub environment, which can require reviewer approval.

See [Supply chain](supply-chain.md) for what is and is not verified.

## Version bump rules

See [Versioning and stability](versioning.md). A change to the [contract](contract.md) is always a major release.
