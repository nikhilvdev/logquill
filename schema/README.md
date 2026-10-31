# LogQuill record schema

`record.schema.json` is the machine-readable definition of one log record
(JSON Schema draft 2020-12). `golden_records.json` is a set of example records
that every LogQuill binding runs its tests against. Together they are what keeps
every language writing the same shape.

Validate a record by loading `record.schema.json` into any JSON Schema 2020-12 validator
and checking the decoded record against it.

`golden_chain.json` is a hash chain written by the tamper-evident plugin over awkward
values (non-ASCII, control characters, small floats, an `llm` block). Every binding
verifies it, which is what proves they hash the same canonical JSON (see
`docs/spec/03-privacy-tracing-otel.md`).

`golden_otel_ids.json` holds the OpenTelemetry span and trace ids derived from LogQuill ids
(UUIDs, awkward hex, non-ASCII), so a trace that crosses languages lines up.

`corpus/` is where the larger shared fixtures go (see `corpus/README.md`).

Field names in `meta` are snake_case (`run_id`, `span_id`, `duration_ms`) in every
language.

## What the golden records check

`golden_records.json` has four groups. A binding's test suite should load the file and
assert:

| Group | Assertion |
|---|---|
| `valid` | validates against `record.schema.json`, and equals itself after a JSON round trip |
| `invalid` | does **not** validate; `why` says which rule it breaks |
| `legacy` | the record parser turns `record` into exactly `parsed` (1.x records gain `schema_version: "1.0"`; a missing `meta` becomes `{}`) |
| `rejected` | the record parser refuses it |

To add a rule, add a case to the right group *and* to `record.schema.json` in one change.

## Versions

`schema_version` is `"<major>.<minor>"`. A new optional field is a minor
change; anything a reader of the previous version couldn't handle is a major
one. A reader accepts records with a newer minor version (keeping fields it
doesn't know) and refuses a newer major version.
