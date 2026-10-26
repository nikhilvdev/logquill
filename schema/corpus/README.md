# Shared corpus

Fixtures that every binding runs, generated in Stage 0 by running the last 2.x Python and
1.x JavaScript releases on one shared input set. Planned groups, from
`docs/spec/` and `docs/rust-core-plan.md`: redaction and PII, content policy, JSON / text /
logfmt output, trace-header parsing, semantic-convention span specs, id derivation,
sampling / rate-limit / adaptive / flight-recorder / alert-dedupe (seeded RNG, fake clock),
run summary, trace tree, tamper chains (including non-ASCII, astral keys, `-0.0`, integers
over 2^53, NaN and infinity), SQL and graph rows, and ECS documents.

The record schema and the first golden files live one level up in `schema/`.
