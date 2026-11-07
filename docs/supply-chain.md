# Supply chain and security posture

What a security reviewer can rely on today. Vulnerability reporting is in
[SECURITY.md](../.github/SECURITY.md).

## In place

| Control | Where |
|---|---|
| Publishing without stored secrets | crates.io, PyPI, npm, RubyGems and NuGet use trusted publishing from the release workflow ([Releasing](releasing.md)) |
| Build provenance | npm packages publish with `--provenance`; PyPI uploads carry attestations from trusted publishing |
| Tag-only releases | The release workflow runs on a tag push; nothing is published by hand |
| Pinned actions | Third-party GitHub Actions in the release workflow are pinned to a commit SHA |
| Dependency updates | Dependabot opens weekly pull requests for Cargo and GitHub Actions |
| Memory safety | The core forbids `unsafe` (`unsafe_code = "forbid"`); checks that need it live in an unpublished crate (a real `fork`, a counting allocator), and the runtime's worker and queue tests run under `miri` in CI |
| Secret scanning | `.gitguardian.yaml` configures scanning; `.gitignore` excludes key and environment files |
| No required runtime dependencies in the host language | Python and npm packages carry the compiled core instead of pulling a dependency tree |
| Data minimisation | Prompt and completion content is off by default; secrets and PII are redacted in Rust before any transport sees a record |
| Tamper evidence | Optional hash-chained, signable logs, checked with `logquill verify` |

## Not a compliance guarantee

The privacy, redaction and tamper-evidence features are technical controls that commonly support compliance work
(for example access, retention and audit requirements). Compliance is a property of a whole system and process, not of a
library. LogQuill does not make an application HIPAA-, PCI- or GDPR-compliant.

## Licences

The 3.0 line is MIT licensed (see [LICENSE](../LICENSE)). Earlier Python 2.0.0 stays FSL-1.1-MIT and all other earlier
releases stay MIT, as published.

## Checking a release yourself

- Compare the version on each registry with the tag and `CHANGELOG.md`.
- On PyPI, open the release page and look for the attestation link.
- For npm, run `npm audit signatures` in a project that depends on the package.
- Check the hash chain of a log with `logquill verify <file>`.
