# Changelog

All notable changes to this project are documented in this file. The history of the
earlier releases is kept in `docs/legacy/python/CHANGELOG.md` (Python 0.3.0–2.0.0) and
`docs/legacy/js/CHANGELOG.md` (JavaScript).

## Unreleased

- Changed: the `develop` branch is now the 3.0 line. The Python library source was removed
  from it and replaced by a small Rust workspace (`logquill-core`, `logquill-cli`), the
  shared record schema and fixtures (`schema/`), and the plan, behaviour specification,
  publishing guide and API-name table (`docs/`). The 2.x Python releases remain on `main`
  and under the `v2.0.0` tag.
