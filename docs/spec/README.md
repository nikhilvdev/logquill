# Behaviour specification

The old Python and TypeScript code will not be in the tree when the Rust work starts.
These files hold what that code does, so the work can be done from them alone.

Facts here were taken from the source of `logquill-python` 2.0.0 and `logquill-js`
(1.0.1 plus the unreleased 2.0 branch) by reading it. They are descriptive: where the
two disagree both behaviours are written down, tagged **P** (Python) and **J**
(JavaScript), and the choice for the new code is in `00-decisions.md`.

| File | Covers |
|---|---|
| `00-decisions.md` | Every place Python and JS differ, with a recommendation |
| `01-records-and-logger.md` | Levels, record shape, parsing, the log pipeline, spans, retries, toggles, exceptions, context |
| `02-plugins.md` | Every plugin: parameters, defaults, algorithms, exact patterns |
| `03-privacy-tracing-otel.md` | Field classes, content policy, canonical JSON and the hash chain, semconv, ids, MCP, ECS, OTLP |
| `04-formatters-config-parsing.md` | JSON, text and logfmt output; parsers; config shape and loader rules |
| `05-runtime-and-transports.md` | Queue, worker, shutdown, serverless, level control, every transport |
| `06-cli-and-viewer.md` | CLI commands, trace tree, viewer API and page |
| `07-adapters-instrumentation-bridges.md` | Framework adapters, SDK patching, logging bridges, browser entry |
| `08-tests-benchmarks-ci.md` | Test inventory, property-test generators, memory budgets, CI and release flows |
| `09-parity-and-gap-closing.md` | The rule that Python is the baseline, what JS must gain, what everyone adopts from JS, and the parity acceptance test |
| `legacy-tests/` | One line per existing test (775 Python, 525 JS): the checklist for the ported SDK tests |

Reference material copied verbatim from the old repos, in `../legacy/`: both READMEs
(the user-facing contract and every documented example), both changelogs, both
migration guides, the viewer page `trace_viewer.html`, and the two benchmark sources.
`../../schema/` keeps the record schema and golden fixtures. `../kibana/` keeps the
Kibana dashboard export.

Conventions: **Rule** is behaviour the new code must reproduce. **Defect** is
something found in the audit that must not be reproduced. Strings in quotes are
verbatim and part of the contract unless noted as a message.
