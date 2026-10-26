# Stage 0 — freeze the old lines and extract the contract

Goal: end with (a) the decisions settled, (b) the old releases preserved as references, and
(c) executable fixtures in `schema/corpus/` that say what "identical" means. No Rust is
written in this stage except the fixture loader. Stage 1 can start in parallel on the groups
whose fixtures already exist (see the table in work package 0.4).

Overview in `docs/rust-core-plan.md`, section 6; behaviour in `docs/spec/`.

## 0.1 Settle the decisions

| Decision | Needed by | Recommendation |
|---|---|---|
| Licence for the shared core | before any crate is published; affects pkg.go.dev (`docs/publishing.md` §3) | author's call; not a Stage 1 blocker |
| Finish JS 2.0 or skip to 3.0 | before Stage 6 | skip unless nearly done |
| Monorepo name | before any registry is configured (`docs/publishing.md` §3) | decide before Stage 5 |
| D3 content-off placeholder text | Stage 1 | one neutral string |
| D4, D19, D31, D32 number and JSON rules | Stage 1 | keep int/float; Python words for NaN in hashes; `null` in JSON lines; UTF-8 |
| D18 legacy camelCase meta rename | Stage 1 | keep in the core |
| D1, D2, D5–D17, D20–D34 | later stages | as written in `docs/spec/00-decisions.md` |

**Do:** read `docs/spec/00-decisions.md`, change any recommendation you disagree with, and
note "settled" next to the rows you accept. Until you say otherwise the code follows the
recommendations.

## 0.2 Preserve the reference implementations

The old code is the oracle for fixture generation. Keep it reachable without keeping it in
the tree.

```bash
# Python reference: the v2.0.0 tag of this repository (stdlib-only for the groups below)
git archive v2.0.0 | tar -x -C /tmp/ref-py        # then: PYTHONPATH=/tmp/ref-py python3 …
# JavaScript reference: the sibling repository (the 2.0 contract is unreleased there)
cd ../logquill-js && npm ci && npm run build      # dist/index.mjs is the oracle
# published JS 1.0.1 is stale for the 2.0 contract; use it only to confirm 1.x records
```

Also: tag the old lines for maintenance (`py-2.x` on `main`), and move the stashed 2.x change
set onto a branch off `main` (`git stash branch fix/2.x-pending`).

## 0.3 Fixture format

One file per group in `schema/corpus/<group>.json`:

```json
{
  "group": "content-policy",
  "description": "What the group checks, in one sentence.",
  "references": { "python": "2.0.0", "js": "<commit of logquill-js>" },
  "cases": [
    {
      "name": "hash policy on an object",
      "input": { "policy": "hash", "value": { "b": 1, "a": [1.0, "é"] } },
      "expected": "sha256:…",
      "differs": { "js": "sha256:…" },
      "rule": "D4 — floats keep their .0 in the canonical text"
    }
  ]
}
```

Bindings read only `input` and `expected`. `differs` and `rule` record where the references
disagreed and which decision settled it, so a reviewer can audit every divergence. Every
language loads the same files through a small loader; the Rust loader is
`crates/logquill-core/tests/corpus.rs` (extend the existing `fixtures.rs`).

## 0.4 Generate the groups

Generator layout (`scripts/corpus/`, written in this stage):

```
inputs/<group>.json     hand-written inputs, including hostile ones
ref_py.py               runs the Python reference on an inputs file, prints outputs as JSON
ref_js.mjs              runs the JavaScript reference the same way
build.py                merges both, applies the settled decisions, writes schema/corpus/<group>.json
```
`python3 scripts/corpus/build.py --check` re-runs both references and fails if a committed
fixture no longer matches, so fixtures cannot drift silently.

| # | Group | Stage that needs it | Source behaviour |
|---|---|---|---|
| 1 | `canonical-json` and `hash-chain` (extend `golden_chain.json`: non-ASCII, `U+007F`, astral keys, `-0.0`, integers over 2^53, `1e16`, `1e-5`, NaN / ±Infinity, nested key order) | 1 | spec 03 |
| 2 | `content-policy` (off / hash / truncate / full; objects, long strings, cycles) | 1 | spec 03 |
| 3 | `field-classes` (the registry, `keys_in_class`) | 1 | spec 03 |
| 4 | `levels` (accepted inputs, error texts) | 1 | spec 01 |
| 5 | `records` (builder, `llm` block validation and warnings, timestamp rounding) — extends `golden_records.json` | 1 | spec 01 |
| 6 | `trace-headers` (W3C, X-Ray, GCP, rejects) | 2 | spec 02 |
| 7 | `otel-ids` (exists) and `semconv-spans` | 2 | spec 03 |
| 8 | `redaction` and `pii` (every pattern; overlapping matches; nested and non-JSON values) | 2 | spec 02 |
| 9 | `formatters` (JSON, text, logfmt, `parse_logfmt`) | 2 | spec 04 |
| 10 | `sampling`, `rate-limit`, `adaptive`, `flight-recorder`, `alert-dedupe`, `run-summary` (seeded RNG, fake clock) | 2 | spec 02 |
| 11 | `trace-tree` and `run-summaries` | 2 | spec 06 |
| 12 | `sql-rows`, `graph-rows`, `ecs` | 2 | spec 03, 05 |

Groups 1–5 unblock Stage 1; do them first. Where the references disagree, the fixture follows
the settled decision and records the old behaviour under `differs`.

## 0.5 Keep the references honest in CI

A scheduled workflow installs the **published** reference releases and re-runs
`build.py --check`, so a reference that changes (a re-release, a yanked version) is noticed.
Add it only after the generator exists.

## 0.6 Housekeeping

- Review `docs/api-names.md` (drafted); settle every row before a binding implements it.
- Install Rust (`rustup`) so the cargo commands in the README can run; today no Rust
  toolchain is installed on the machine these scaffolds were written on, so **none of the
  Rust has been compiled**.
- Fix the pre-commit environment (`pre-commit` plus a Rust toolchain) so commits on
  `develop` run the hooks.

## Exit criteria

- The decisions in 0.1 are marked settled.
- References are reachable and reproducible; the stash is on its own branch.
- Groups 1–5 exist in `schema/corpus/`, pass under both references (or record `differs`), and
  `build.py --check` is green.
- Every divergence between the two references is listed with its governing decision.
- The cargo commands in the README run green on a machine with Rust installed.
