# Versioning and stability

## One version for every language

All packages (crates, PyPI, npm, RubyGems, NuGet, Maven Central, Go, Swift) share **one lockstep
version number**, following [Semantic Versioning](https://semver.org). `3.2.1` of the Python
package and `3.2.1` of the Node package speak the same record contract and have the same features.

CI checks that every manifest agrees on the version before a release.

## What each part of the version means

| Bump | When |
|---|---|
| **Major** | Anything in the [cross-language contract](contract.md) changes (record shape, level weights, field names, the `llm` block, the content policy, hash-chain format), or a public API is removed or changes meaning. A contract change is *always* a major bump, even if no code in one binding changed |
| **Minor** | New backward-compatible features: a transport, a plugin, an adapter, a CLI command, a new optional field |
| **Patch** | Bug fixes and documentation, with no change in the contract |

## What is covered by the stability promise

Stable within a major version:

- The record shape and `schema_version`, the six levels, the `llm` block and the `meta` field names in the contract.
- The public names in [API names](api-names.md).
- The `logquill` command and its flags.
- The on-disk formats: JSONL lines, the hash-chain format read by `logquill verify`, the SQLite log schema.

Not covered:

- Anything marked experimental in a binding's README.
- Internal modules and the layout of private crates.
- Output formatting of the human-readable `text` formatter and `dev` view (use JSON or logfmt for machines).
- OpenTelemetry GenAI attribute names. The upstream conventions are still in development, so LogQuill pins one
  version in one file (`semconv`) and may move it in a **minor** release; the bump is announced in the changelog.

## Records over time

- A record without `schema_version` is a 1.x record and is read as `"1.0"`. Readers keep accepting older versions
  within the same major line.
- Writers always stamp the current `schema_version`.

## Pre-releases

Release candidates (`X.Y.Z-rc.N`) are not picked up by default; install them explicitly. They carry no stability promise.

## Deprecation

A feature to be removed is marked deprecated in a minor release, with a changelog entry and a replacement named, and
is removed no earlier than the next major release.

## Supported lines

See [Support](../.github/SUPPORT.md) for which versions receive fixes.
