# Stage 1 — core: records and integrity

Goal: `logquill-core` can build, read, canonicalise, hash, sign and verify records exactly as
the 2.x Python release does (and as the shared fixtures say), with no I/O and no threads.
Behaviour is specified in `docs/spec/01-records-and-logger.md` and
`docs/spec/03-privacy-tracing-otel.md`. The Rust CLI gets one command, `verify`.

## What is in and out

| In (this stage) | Out (later stages) |
|---|---|
| levels, record shape, timestamp, `parse_record`, `llm` block | the `Logger` pipeline, plugins, queue, transports (Stage 2–3) |
| canonical JSON, hash chain, head signing, verification | redaction and PII patterns (Stage 2) |
| field classes, content policy | formatters, parsers, config (Stage 2) |
| retry tracker, enable/disable rules | CLI commands other than `verify` (Stage 4) |
| `logquill verify` | bindings (Stages 5–8) |

## Start conditions

- Rust installed; the three cargo commands in the README are green on the existing scaffold
  (fix any first-build issues in the stubs first).
- Decisions D3, D4, D18, D19, D31, D32 settled (`docs/stages/stage-0.md`, 0.1).
- Fixture groups 1–5 from Stage 0 exist, or are written alongside each package below.

## Work packages (in order; one pull request each)

**1.1 Module layout and dependencies.** Split `crates/logquill-core/src/lib.rs` into
`level.rs`, `record.rs`, `llm.rs`, `canonical.rs`, `chain.rs`, `policy.rs`, `retry.rs`,
`toggle.rs`, with `lib.rs` re-exporting. Add dependencies deliberately, current versions at
the time: `serde_json` (with `arbitrary_precision` so `12.0` and `12` stay distinguishable, and
`preserve_order`), `sha2`, `hmac`. Anything else needs a reason in the PR.

**1.2 Levels.** `Level` (done), `Level::parse` accepting a name (any case) or a weight, with
the error texts in spec 01, `Display`. Fixture group `levels`.

**1.3 Records and the timestamp.** `Record` (ordered fields, `meta` as a JSON object, optional
`llm`), a `Clock` trait injected for tests, the timestamp algorithm in spec 01 (round to whole
microseconds, then truncate to milliseconds, `Z`). Property test: output always matches
`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$`.

**1.4 `parse_record`.** Every rule and error text in spec 01 (non-object, version regex, newer
major, string fields, level name, `meta`, `llm`, unknown keys kept, legacy `"1.0"`, camelCase
rename for legacy records per D18). Run `golden_records.json` completely: `valid` round-trips,
`invalid` fails validation against `record.schema.json`, `legacy` equals `parsed`, `rejected`
errors. Add a JSON Schema validator as a **dev**-dependency only.

**1.5 The `llm` block.** `build_llm_block` with the per-field rules and warning texts, returning
the warnings to the caller (the core has no logger).

**1.6 Canonical JSON.** The hardest package; see "Risks" below. Implement spec 03 exactly:
`", "` / `": "` separators, keys by code point, ASCII-only escapes with surrogate pairs and
`\u007f`, integers verbatim, floats by Python's `repr` rules, `NaN` / `Infinity` words,
cycles impossible (values are trees). Fixture group `canonical-json`; the existing
`golden_chain.json` must verify.

**1.7 Hash chain.** `hash_record(record, prev)`, `verify_chain` returning the detailed result
(spec 03 texts), `sign_head`, `verify_head_signature` (constant time), `verify_signed_chain`.
Cross-version test: a chain written by Python 2.0.0 verifies in Rust and the reverse (a test
helper that shells out to the pinned reference in CI, skipped when it is absent).

**1.8 Field classes and content policy.** The registry, `keys_in_class`, `apply_content_policy`
with the settled placeholder (D3), `sha256:` digests over canonical text, truncation marker.
Fixture groups `field-classes` and `content-policy`.

**1.9 Retry tracker and toggle rules.** Both are small pure state machines (bounded to 1 024
entries; most-specific-rule-wins). Unit tests from spec 01.

**1.10 `logquill verify`.** Stream a JSONL file, skip unparseable lines silently, print the
success / failure lines in spec 06, exit codes 0 / 1, `--sign-key` / `--signature`. Add the
argument parser dependency only now.

**1.11 Fuzz and property tests.** `proptest` mirroring the old hypothesis generators
(spec 08); `cargo-fuzz` targets for `parse_record` and the canonicaliser (both must never
panic).

## Exit criteria (from the plan)

- `golden_records`, `golden_chain` (extended) and groups 1–5 pass.
- A log signed by Python 2.0.0 verifies in Rust and the reverse.
- `cargo fuzz` runs the parser and canonicaliser for a fixed budget without a panic.
- fmt, clippy and tests are green on all three operating systems in CI.

## Risks to watch

- **Python float `repr`.** Shortest round-trip digits, fixed notation when the decimal point
  position is in (−4, 16], otherwise exponent form with at least two exponent digits
  (`1e-05`, `1e+16`). Rust's `{:e}` gives the shortest digits; lay them out yourself and test
  against the fixtures, including `5e-324`, `1.7976931348623157e308`, `0.1`, `100.0`, `-0.0`.
- **Preserving int vs float.** Do not round-trip through `f64`: keep the source lexeme class.
  Integers beyond `i64` must survive.
- **Surrogates.** Python strings can carry lone surrogates; JSON from files cannot, so reject
  or replace them at the boundary and say which in a test.
- **Key order.** `meta` keeps insertion order for output but is sorted for hashing; a test
  must prove both.
- **Panics.** Nothing in the core may panic on hostile input; return errors.

## Ready check

```bash
rustc --version && cargo --version     # installed
cargo fmt --all --check && cargo clippy --workspace --all-targets -- -D warnings && cargo test --workspace
git status --short                      # clean apart from the work in progress
```
