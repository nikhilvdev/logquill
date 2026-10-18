# Changelog

All notable changes to this project are documented in this file.

## 2.0.0 - 2026-10-18

- Fixed: `ElasticsearchTransport` now actually writes [Elastic Common
  Schema](https://www.elastic.co/guide/en/ecs/current/ecs-reference.html)
  fields (`@timestamp`, `log.level`, `service.name`, and, when present,
  `trace.id`/`span.id`/`error.message`/`error.stack_trace`) alongside
  LogQuill's own fields, via a new internal `ecs_mapper` — these are what
  make Kibana's default Discover view, dashboards, and alerting rules
  work on this data with zero manual field mapping, and were documented
  as part of this transport from early on but never actually implemented.
  The document body is now always JSON, built directly from the ECS
  mapping; the `formatter` constructor parameter (inherited from
  `BatchingTransport`) no longer shapes it, since Elasticsearch's bulk API
  requires one JSON object per line regardless of formatter.
- Docs and 2.0.0 release prep: a starter Kibana dashboard
  (`docs/kibana/logquill-dashboard.ndjson`) — an index pattern, a recent-logs
  saved search, and two visualizations (records by level over time, records
  by service) — importable via Kibana's Saved Objects UI, built on the ECS
  fix above; `MIGRATING.md` extended to cover the license change, the
  module reorganization below, and every feature added since the 1.x → 2.0
  contract work, with pointers into the README rather than duplicating it;
  every README code example re-verified against the current source, which
  caught and fixed two real bugs: the `span(capture_state=...)` example
  didn't account for `state_diff` being a `content`-classed field (off by
  default since the privacy policy work), and the `diagnose=True` example
  passed a secret as a literal at the call site, which Python's traceback
  source-line echo reveals regardless of `redact_local` — rewritten to bind
  it to a correctly-named variable first, which is also what the feature
  actually protects against.
- **Breaking: license change.** Starting with this release, `logquill` is
  source-available under [FSL-1.1-MIT](LICENSE) (the Functional Source
  License, MIT Future variant) instead of MIT. Free for internal use,
  non-commercial education/research, and professional services you provide
  to a licensee; a commercial product or service that competes with
  logquill itself needs a separate commercial license from the author. Each
  version converts to plain MIT two years after that version's own release.
  **This is not retroactive** — 1.0.0 and every earlier release already on
  PyPI remain MIT, unaffected. See the [License](README.md#license) section
  of the README for the full explanation.
- Performance and platform hardening:
  - `JSONFormatter(backend="orjson")` (`pip install logquill[fast]`) swaps
    the stdlib `json` module for the Rust-backed `orjson` library — same
    compact output, less CPU per call under sustained logging volume.
    `orjson` is imported lazily and only if you ask for it; zero required
    dependencies is unaffected. `orjson` doesn't currently support
    free-threaded Python at all (its own build script refuses it, not a
    missing-wheel gap), so `backend="json"` is what a free-threaded
    interpreter gets regardless of what's installed.
  - Thread-safety audit and fix: `BatchingTransport` (the shared base for
    every SQL/NoSQL/queue/cloud transport), `HTTPTransport`, and
    `FileTransport` now lock around their internal buffer/file-handle
    mutation. Previously, a `Logger(async_dispatch=False)` (the default)
    shared across application threads could race on these — two threads
    passing the batch-size check before either flushed, or a rotation
    closing a file handle out from under a concurrent write — silently
    losing or duplicating records. Confirmed as a real, reproducible loss
    (not just theoretical) under free-threaded Python before the fix, and
    confirmed fixed after, via a 16-thread burst test. `AsyncWorker` was
    already correctly locked (a `threading.Condition`-guarded queue) and
    needed no change.
  - `Logger.opt(lazy=..., depth=...)` now caches the returned `OptLogger`
    per `(lazy, depth)` combination instead of allocating a fresh one on
    every call — the common idiom is calling it inline on every log call
    (`logger.opt(lazy=True).debug(...)`), so this removes a wrapper-object
    allocation from that path. Bounded at 32 cached combinations per
    `Logger` so a caller computing `depth` dynamically can't grow it
    without limit. A new benchmark metric (`peak_bytes_filtered_opt_call`)
    guards the filtered-and-deferred path specifically.
  - Stamping a record no longer re-formats the date on every call: the
    `YYYY-MM-DDTHH:MM:SS` part of the timestamp is built once per second
    and reused, with only the milliseconds formatted per record. That was
    the single largest cost in a log call (about 1.2 µs of roughly 3.7 µs);
    it is now about 0.2 µs. The timestamp text is unchanged.
  - `import logquill` is about twice as fast (roughly 43 ms down to 20 ms)
    and no longer loads `asyncio`, `urllib.request`, `ssl`, `smtplib`,
    `sqlite3` or `http.client`, which matters most for the CLI and for
    serverless cold starts. The transports (other than console, file and
    the batching base) and the Slack, PagerDuty, email, Apprise, StatsD,
    Prometheus and OTel-metrics classes are now imported the first time you
    use them (PEP 562 module `__getattr__`) rather than when the package
    loads. Nothing about how you use them changes — `from logquill import
    HTTPTransport`, `logquill.SlackAlertPlugin`, `__all__` and the
    published API reference all work exactly as before, and type checkers
    still see every name. `config`'s `{"type": "http"}` shortcut and
    `AsyncWorker`'s `drain_async` now import what they need at the point
    they need it.
  - The full test suite and the memory-budget benchmark suite now run in
    CI on free-threaded Python (`3.13t` and `3.14t`, PEP 703) alongside the
    existing GIL-enabled matrix. `hypothesis`'s native extension doesn't
    yet ship (or build) wheels for free-threaded Python below 3.14 — an
    upstream PyO3 constraint — so `tests/conftest.py` turns a missing
    `hypothesis` into a clean skip of the property-based suites rather
    than a collection error, and the `3.13t` CI job installs a smaller
    dependency set accordingly.
- Internal reorganization: the top-level modules that had grown past a
  single implementation each are now grouped into themed subpackages —
  `core/` (`logger`, `levels`, `records`, `context`, `exceptions`,
  `handler`, `opt`), `runtime/` (`worker`, `shutdown`, `toggle`,
  `runtime_level`, `serverless`), `tracing/` (`span`, `retry`, `mcp`,
  `semconv`), `cli/` (`cli`, `webui`, `trace_tree`, `static/`), and
  `privacy/` (`privacy`, `audit`). Every name already exported from the
  top-level `logquill` package (`from logquill import Logger`, etc.) is
  unaffected; only a direct deep import of one of these modules (e.g.
  `from logquill.mcp import inbound, propagate`) needs its path updated
  to the new location (`from logquill.tracing.mcp import ...`). The CLI's
  packaged entry point moved from `logquill.cli:main` to
  `logquill.cli.cli:main`, transparent to anyone invoking the installed
  `logquill` command.
- Metrics bridge and graph transports:
  - `MetricsPlugin` turns log patterns into metric emissions — the
    logs-to-metrics side of the three-pillars observability model. Every
    record at `error_at` (default `ERROR`) or above increments an error
    counter tagged by `logger`; any record carrying `meta.duration_ms`
    (every span-closing record) feeds a duration histogram. Runs in
    `after_log`, so a slow or unreachable backend never delays the record
    it's reporting on, and a raising backend is caught by the plugin
    pipeline the same as any other plugin. `backend` is a small
    `MetricsBackend` protocol (`increment`/`observe`), resolved lazily to
    `OTelMetricsBackend()` by default so importing `logquill` never pulls
    in OpenTelemetry on its own. `StatsDBackend` (stdlib `socket`,
    DogStatsD-style tags) and `PrometheusPushgatewayBackend` (stdlib
    `urllib`, accumulates in memory and only sends a batched `PUT` on
    `.push()`/`.close()`, never per-metric) cover teams not yet on OTel.
  - `BaseGraphTransport` writes a run's span tree directly into a graph
    database instead of a flat log: every record becomes a `:LogRecord`
    node, `meta.parent_span_id` becomes a `PARENT_OF` edge, and
    `meta.run_id` (falling back to `meta.trace_id`) groups a run's nodes
    under a `:Session` via `HAS_RECORD` — all through one shared,
    backend-portable Cypher query, so reconstructing an agent run's exact
    call tree is a traversal, not application code walking a JSONL file.
    Three concrete backends ship on top of it: `Neo4jTransport`
    (`pip install logquill[neo4j]`), `FalkorDBTransport`
    (`pip install logquill[falkordb]`), and `NeptuneTransport` (via
    `boto3`'s openCypher support, reusing the existing `aws` extra).
    Switching backends means swapping the transport, not touching any
    call site. `KuzuTransport` was scoped for this release but
    deliberately not built: Kuzu's own repository was archived by its
    maintainers on 2025-10-10 with no release schedule, and building
    against an archived database contradicts the "verify it's still
    maintained before committing to it" bar every other dependency here
    is held to.
- Runtime intelligence:
  - `FlightRecorderPlugin` generalizes `SamplingPlugin`'s tail-based
    elevation from "a record sampling happened to drop" to "a record below
    the level you actually want shipped" — buffers low-level records
    (`DEBUG`/`TRACE`) per run and ships the whole buffered trail only if
    that run later reaches an error level, so a run at `DEBUG` costs
    nothing to ship unless it actually fails. Bounded the same way tail
    elevation is (`max_buffered_records`/`max_runs`), and verified to stay
    memory-bounded under a sustained multi-run burst.
  - `RunSummaryPlugin` emits one record — total tokens, cost, tool calls,
    retries, and errors — when a run's outermost span closes, so a
    dashboard doesn't need to re-walk every record for a run's totals.
  - `AdaptiveSamplingPlugin` always keeps errors and slow spans, samples
    everything else, and caps ordinary traffic to a bytes-per-second
    budget — adjusting its own rate up when there's room to spare and down
    when it's saturated each window, based on demand rather than bytes
    actually emitted (which stop growing once the budget saturates, and
    would otherwise read a saturated gate as "plenty of room").
  - `install_signal_level_handler`, `LevelFileWatcher`, and
    `LevelEnvWatcher` change a `Logger`'s level without restarting the
    process — a signal that re-reads an environment variable, a polled
    file, or a polled environment variable (only useful when something
    inside the same process is the one changing it — documented plainly,
    since that's a real limit worth knowing about, not a gap to paper over).
- Privacy, content policy, and an audit trail:
  - Every `Logger` now has a `content_policy` (`"off"` by default \| `"hash"`
    \| `"truncate"` \| `"full"`), governing the content fields an LLM call or
    tool call can carry (`meta.input_messages`/`output_messages`/
    `system_instructions`/`tool_arguments`/`tool_result`/`state_diff`).
    Applied before any plugin or transport ever sees the record, so a seeded
    secret inside a prompt never reaches a transport unless you opt in.
    `.child()` inherits the parent's policy.
  - Field classification (`logquill.privacy.FIELD_CLASSES`: `secret` \|
    `pii` \| `content`) lets a redaction rule target a class instead of
    listing keys by name: `RedactPlugin(classes=["secret", "content"])`.
    `RedactPlugin`'s existing default key set is unchanged, now sourced from
    the same registry.
  - `PIIRedactPlugin` gained patterns for well-known API-key/secret shapes —
    OpenAI/Anthropic keys, AWS access key IDs, GitHub/Slack tokens, JWTs,
    full PEM private-key blocks, generic `Bearer` tokens — alongside its
    existing email/SSN/credit-card/phone patterns. Also fixed: overlapping
    matches between two patterns (e.g. a phone-shaped digit run inside a
    Slack token) used to redact in a sequence of independent substitutions,
    which could leave a value only half-redacted; matches are now resolved
    across all patterns in one pass before anything is replaced.
  - `TamperEvidentPlugin` gained `.head_hash`/`.sign_head(key)` and the
    module-level `verify_chain_detailed`/`verify_signed_chain`/`sign_head`/
    `verify_head_signature`. Hash-chaining alone can't catch a log file
    being truncated — the remaining chain is perfectly self-consistent, just
    shorter — so signing the chain's current head and checking it
    separately closes that gap. `logquill verify <file>` (optionally
    `--sign-key`/`--signature`) wraps this from the command line, reporting
    where and why a chain broke.
  - `AuditLogger` bundles `RedactPlugin`, `PIIRedactPlugin`, and
    `TamperEvidentPlugin` with content capture off by default — the
    technical controls an audit trail commonly needs, in one call. Its
    `.child()` shares the parent's hash chain rather than starting a second,
    interleaved one. Same scope-honesty note as the rest of the plugin
    pipeline: none of this is a compliance guarantee on its own.
  - The record contract gained `meta.tool_arguments`/`meta.tool_result`,
    and documents which fields the content-capture policy governs.
- Local-first trace viewer:
  - `logquill trace <run_id> --file logs.jsonl` reconstructs and prints one
    agent run's span tree, annotated with each span's own duration and the
    token/cost totals rolled up from everything nested under it. It streams
    the file line by line, so tracing one run out of a multi-gigabyte log
    costs memory proportional to that run, not the file (a gigabyte-scale
    test asserts this). `--json` prints the same tree as nested data instead.
  - `logquill serve --file logs.jsonl` (or `--db logs.sqlite` for a
    `SQLiteTransport` database) runs a small local web UI — run list, a
    combined span-tree/waterfall view, search, and a level filter — built
    entirely on the stdlib (`http.server`, `sqlite3`): no new dependency, no
    account, nothing leaves the machine. Reading from a SQLite database never
    shows token/cost annotations, since that transport's fixed schema doesn't
    store the `llm` block.
  - `logquill dev logs.jsonl` follows a file like `tail -f`, but live-renders
    the current run's span tree (colorized, screen-cleared between redraws)
    instead of flat lines, following whichever run is most recently active
    unless `--run-id` pins it to one.
- Auto-instrumentation, an OpenAI Agents SDK adapter, and MCP trace propagation:
  - `logquill.instrument.anthropic(logger)` / `.openai(logger)` / `.litellm(logger)`
    patch the Anthropic, OpenAI, and litellm Python SDKs so every LLM call they
    make anywhere in the process emits a `logger.llm_call(...)` — no call-site
    changes. Each has a matching `.uninstrument()`, lives behind its own
    optional extra (`logquill[instrument-anthropic]`, `[instrument-openai]`,
    `[instrument-litellm]`), and is imported lazily: `import logquill.instrument`
    never imports a provider SDK. Streaming calls are a documented gap in this
    release — passed through untouched rather than partially instrumented.
  - `OpenAIAgentsAdapter` (`pip install logquill[openai-agents]`) maps the
    OpenAI Agents SDK's `RunHooks` the same way the existing adapters map their
    frameworks: every agent activation (including a handoff's target) is its
    own `invoke_agent` span, and `on_llm_end` becomes a real `.llm_call()` with
    token usage, so `OTLPTransport` exports a full run — spans, tokens, cost —
    with zero manual logging calls.
  - `logquill.mcp` (`propagate()`/`inbound()`) propagates trace context over an
    MCP request's `_meta` field and stamps `meta.mcp.*` on records logged while
    handling one, with no dependency on the `mcp` package itself. A client's
    `run_id` rides along informationally as `meta.mcp.run_id`; it never
    overrides the handling process's own `RunPlugin` run id.
  - The record contract gained `meta.mcp.run_id`.

- LLM calls and OpenTelemetry export:
  - `logger.llm_call(model=, tokens_in=, tokens_out=, cost_usd=, latency_ms=,
    finish_reason=)` records an LLM call with its numbers in the record's
    first-class `llm` block. A value that breaks the contract is dropped with a
    warning instead of raising.
  - `OTLPTransport` (`pip install logquill[otel]`) exports agent tracing as real
    OpenTelemetry spans through the SDK: `span()` blocks, tool `.action()`s and
    LLM calls, with the ids, parents and timings the records carry, so a
    collector shows exactly the tree that was logged. Spans are named and
    attributed per the OpenTelemetry GenAI conventions (`invoke_agent`,
    `execute_tool`, `chat`, with token counts, model and finish reason).
  - `OTelLogsTransport` sends every record as an OTLP log record, with the same
    trace and span ids so logs and spans join up.
  - The convention names live in one file, `logquill/semconv.py`, pinned to a
    named release (semantic-conventions 1.44.0), because the GenAI conventions
    are still experimental and have already renamed attributes.
    `OTEL_SEMCONV_STABILITY_OPT_IN` is honored, `semconv_version="legacy"` picks
    the older names, and old and new names are never emitted together. Prompt
    and completion text is opt-in only.
  - `span(name, capture_state=...)` records what changed during a block as
    `meta.state_diff`, and a tool `.action()` that is reopened before it
    succeeded gets `meta.retry_count` automatically.
  - The record contract gained optional `meta` fields for this: `tool`,
    `tool_call_id`, `provider`, `agent_name`, `agent_id`, `response_model`,
    `operation`, and opt-in `input_messages` / `output_messages`.
  - Type-checking no longer depends on whether OpenTelemetry is installed.
- **Breaking: the record shape and the Python floor changed.** See
  [MIGRATING.md](MIGRATING.md).
  - logquill now requires **Python 3.10 or newer** and is tested on 3.10–3.14.
    Python 3.8 and 3.9 are end-of-life; pip on those interpreters keeps
    installing 1.x.
  - Every record now carries `schema_version` (`"2.0"`). Nothing was renamed or
    removed, so a reader only breaks if it insists on exactly the five 1.x
    keys. `parse_record()` reads 1.x and 2.x records alike, labelling a 1.x
    record `"1.0"`.
  - New reserved fields, shared with `logquill` on npm: a top-level `llm` block
    (`model`, `tokens_in`, `tokens_out`, `cost_usd`, `latency_ms`,
    `finish_reason`) for LLM calls, plus `meta.retry_count`, `meta.state_diff`
    and `meta.mcp.server`/`meta.mcp.tool`. The text and logfmt formatters show
    the `llm` block. Nothing writes these on its own yet.
  - `TamperEvidentPlugin` now covers `schema_version` and `llm` in the hash, so
    editing either is caught; hash chains written by 1.x still verify.
  - Fixed: the New Relic transport rebuilt each record from its five 1.x fields
    and would have dropped `schema_version` and `llm`; it now copies the record.
  - The record format now has a machine-readable definition,
    `schema/record.schema.json` (JSON Schema 2020-12), and a shared file of
    golden records, `schema/golden_records.json`. `tests/test_contract.py`
    checks the schema, the golden records, the parser, and everything the
    logger actually writes against each other, so drift between the Python and
    JavaScript packages now fails a test instead of relying on a reviewer to
    notice.

- Added the 1.0 features that hadn't shipped yet, plus hardening:
  - `logger.opt(lazy=True)` defers callable `meta` values until a record is
    really going to be emitted, so an expensive `DEBUG`/`TRACE` argument costs
    nothing when the level filters the call out. A callable that raises leaves
    a placeholder in the record instead of crashing the caller.
    `logger.opt(depth=N)` adds `meta.caller` (`module`, `function`, `line`,
    `file`) naming the code that logged, `N` frames up, so a call made from a
    wrapper or decorator reports the wrapper's caller.
  - `logquill.disable(name)` / `logquill.enable(name)` switch off a logger and
    everything nested under it (most specific rule wins), so a library that
    logs through LogQuill can be silent in its host application by default —
    `logquill.disable(__name__)` — and the application can turn it back on.
  - Two new formatters: `TextFormatter` (a human-readable entry per record,
    with tracebacks on the lines below) and `LogfmtFormatter` (single-line
    `key=value` output; values are quoted so a record is always one line).
    Formatters now live in the `logquill.formatters` package;
    `logquill.formatter` still works. A transport's `options` in a config file
    can name one: `{"formatter": "text"}`. `logquill tail` now prints
    tracebacks the same way `TextFormatter` does.
  - `parse(source, pattern, cast=...)` extracts structured fields from a log
    file with a regex — including legacy and third-party formats — streaming
    line by line. `parse_logfmt()` reads logfmt back, and `TEXT_LOG_PATTERN`
    reads `TextFormatter` output.
  - `AppriseAlertPlugin` (`pip install logquill[apprise]`) sends alerts
    through Apprise, reaching 100+ notification services with the same
    deduplication and non-blocking behavior as the other alerting plugins.
  - Every `Logger` now flushes and closes its transports at interpreter exit
    (an `atexit` hook), so a script that never calls `close()` no longer loses
    its last queued records or a batching transport's unsent batch. Opt out
    with `Logger(flush_at_exit=False)` or `"flush_at_exit": false` in config.
  - `diagnose=True` on any `Logger` method adds each traceback frame's local
    variable values. Off by default, with an explicit warning in the docs and
    once per process in the log: it can leak sensitive data. Captured values
    go through `RedactPlugin` and `PIIRedactPlugin` *before* the traceback is
    formatted (via a new optional `Plugin.redact_local` hook), and a plugin
    whose hook raises masks the value instead of showing it.
  - `HTTPTransport(backend="aiohttp")` sends over one reused keep-alive
    connection, giving the `http` extra a purpose. Also, `HTTPTransport` now
    bounds its buffer by `max_bytes` as well as `batch_size`, and a failed
    send is logged with an actionable message and the batch dropped, rather
    than raising into the code that logged.
  - Fixed: the async queue's "dropping records" warning could stay silent for
    the first minute after a process started, because its rate limiter
    compared against a monotonic clock whose zero point is arbitrary.
  - Fixed: passing a malformed `exc_info` (for example a forwarded dict that
    happens to carry a bad `exc_info` key) raised out of the log call. It is
    now ignored with a warning, like any other bad `meta`.
  - A memory-budget suite (`pytest benchmarks`, its own CI job) fails the
    build if a log call, a level-filtered call, or a burst into a stalled
    sink uses materially more memory than it does today. New tests burst
    30,000 records at a stalled transport under each backpressure policy and
    assert exactly which records survive, and `hypothesis` coverage now
    extends to the formatters and transports.

## 1.0.0 - 2026-09-05

- First stable release: bumped the `Development Status` classifier from
  `3 - Alpha` to `5 - Production/Stable`, matching `1.0.0`.
- Packaging polish:
  - Confirmed the `py.typed` marker ships correctly inside the built wheel
    (verified by installing that wheel into a fresh virtualenv and running
    `mypy --strict` against a script importing the installed package, not
    the local checkout) and that `pyproject.toml`'s keywords, project
    URLs, and optional-dependency extras were already complete.
  - Fixed a real sdist-packaging bug found while verifying the above: the
    sdist was bundling whatever untracked local files happened to sit in
    the working tree at build time — including the local `hypothesis`
    test cache (`.hypothesis/`, 145 files) and other per-machine tool
    state — alongside the actual source, since `hatchling`'s default
    sdist selection includes anything not explicitly `.gitignore`d, and
    not every local artifact was. Replaced that with an explicit
    `[tool.hatch.build.targets.sdist]` allowlist naming exactly the
    paths the package needs (`logquill/`, `tests/`, docs, license,
    `pyproject.toml`), so a sdist built from any contributor's machine
    stays limited to actual project files regardless of what else is
    sitting in the working tree.
  - Verified `python -m build` produces a clean wheel and sdist, `twine
    check dist/*` passes on both, and installing the built wheel into a
    fresh virtualenv works end to end — import, logging calls, and the
    `logquill` CLI entry point all function against the installed package.
- Phase 9, docs, complete:
  - Every public class and function across the package now has a docstring
    explaining what it does and why you'd reach for it — not a restatement
    of its name — matching the bar already set by the existing public API.
  - A full API reference, generated straight from those docstrings with
    [pdoc](https://pdoc.dev) (`pip install logquill[docs]`), is published to
    GitHub Pages and rebuilt automatically on every push to `main` via
    `.github/workflows/docs.yml`.
  - README: a new "Kubernetes" section explains why `ConsoleTransport`
    (stdout/stderr, captured by the node's log agent) belongs in a
    container instead of `FileTransport` (writes to an ephemeral
    filesystem nothing aggregates), and how to avoid losing queued records
    to `SIGTERM` when `async_dispatch=True` is combined with a container's
    termination grace period.
- Phase 8, CLI, complete:
  - `logquill tail <file> [--level=] [--json] [-f/--follow] [-n/--lines]` — a
    `logquill` console-script for tailing a JSONL log file in local dev.
    Human-readable output by default, colorized by level to match
    `ConsoleTransport`; `--json` prints each matching record as a raw JSON
    line instead. `--level` filters to that level and above; `-n` limits to
    the last N matching records; `-f`/`--follow` keeps polling the file for
    newly appended records, for a `tail -f`-style live view. A line that
    isn't valid JSON, or isn't a JSON object, is skipped with a warning on
    stderr instead of aborting the whole tail.
- Phase 7, advanced context & stdlib bridge, complete:
  - `bind_context(**values)` — a `contextvars`-based context manager that
    merges `values` into every `Logger` call underneath it, through any
    method and any number of function calls deep, without threading them
    through each signature by hand. Isolated per thread/asyncio task;
    nested blocks merge, with the innermost value winning on key collision
    (an explicit call-site `meta` value still wins over anything bound this
    way). `current_context()` reads the merged dict directly.
  - `exc_info=` on every `Logger` method (`logger.error("failed",
    exc_info=e)`) — accepts the same shapes stdlib `logging` does (an
    exception instance, `True` for the exception currently being handled,
    or an explicit `(type, value, traceback)` tuple), formats a traceback
    into `meta["stack"]`, and is never kept in `meta` as the raw exception
    object, since that isn't serializable. `format_exc_info()` is exported
    directly for anything that wants the same formatting standalone.
  - `LogQuillHandler` — a `logging.Handler` subclass that bridges stdlib
    `logging` calls (including from third-party libraries) into a LogQuill
    `Logger`, so they flow through the same transports and plugin pipeline
    as a native `.info()`/`.error()`/... call. `extra=` fields land in
    `meta`; an attached `exc_info` is formatted into `meta["stack"]` the
    same way the `Logger`'s own `exc_info=` kwarg is. Level filtering still
    applies on top of whatever the stdlib logger/handler's own level is set to.
  - `RateLimitPlugin(max_records, per_seconds)` — drops records once a key
    (by default `(logger, level)`, or a custom `key_func`) exceeds
    `max_records` within a rolling per-key window, to cap a noisy loop
    without silencing the logger's other messages. Bounded by `max_keys`
    distinct keys tracked at once, evicting the least-recently-seen key's
    window to make room for a new one.
- Phase 6, async worker, shutdown & serverless safety, complete:
  - `AsyncWorker` — a bounded, in-memory queue backed by a single daemon
    thread, with a configurable `backpressure` policy for what happens once
    that bound is hit under a sustained burst: `drop_oldest` (default,
    evicts the oldest queued item), `drop_newest` (discards the item that
    just overflowed the queue), or `block` (the submitting thread waits for
    space instead of dropping anything). Either drop policy logs at most one
    warning per minute while actively dropping, not one per drop.
  - `Logger(async_dispatch=True, max_queue_size=10_000, backpressure=
    "drop_oldest")` — moves each record's transport writes and `after_log`
    plugin hooks onto that background thread, so `.info()`/`.error()`/...
    return without waiting on a transport's I/O; `before_log` hooks still
    run synchronously, since a later hook or transport needs to see their
    result in order. `Logger.child()` shares its parent's worker rather than
    starting a second background thread.
  - `Logger.flush(timeout=None)` / `await Logger.flush_async(timeout=None)`
    — drain any queued records and flush each transport's own internal
    buffer (`Transport.flush()`, a new no-op-by-default hook; already
    matched by `BatchingTransport`'s existing buffered-batch flush) without
    closing anything, so the logger stays usable right after. `Logger.close
    (timeout=5.0)` now drains the queue (up to `timeout`) before closing
    every transport.
  - `with_lambda(logger_or_loggers, timeout=5.0)` — wraps a handler so
    `flush()`/`flush_async()` runs before the handler's result or exception
    reaches the caller, covering both sync and `async def` handlers.
    Flushes rather than closes, since a warm serverless container reuses
    the same `Logger`/transports on its next invocation. `with_cloud_function`
    and `with_azure_function` are the same decorator under a name that
    reads naturally at each platform's own handler definition — the
    flush-before-return behavior needed is identical across all three.
  - `load_config`/`logger_from_file`/`logger_from_env` accept the new
    `"async_dispatch"`/`"max_queue_size"`/`"backpressure"` config keys,
    mapped straight onto the matching `Logger` constructor arguments.

## 0.5.0 - 2026-09-01

- `LangGraphAdapter` (`pip install logquill[langgraph]`) — corrects an
  overstatement in the 0.4.0 entry below: LangGraph nodes run as ordinary
  LangChain `Runnable`s, so `LangChainAdapter` alone already captures node
  execution, but LangGraph also has its own checkpoint lifecycle —
  `on_interrupt`/`on_resume`, fired when a graph pauses on an `interrupt()`
  call (e.g. for human review) and later resumes from a persisted
  checkpoint — that LangGraph dispatches only to handlers that are
  instances of its own `GraphCallbackHandler`; a plain `BaseCallbackHandler`
  subclass (all `LangChainAdapter` is) never receives them. `LangGraphAdapter`
  is `LangChainAdapter` plus those two, mapped to `.observation
  ("graph_interrupted", ...)`/`.action("graph_resumed", ...)` carrying
  `checkpoint_id`/`status`/`checkpoint_ns`/pending `Interrupt` payloads, with
  the event's own `run_id` as `parent_span_id`. `pip install
  logquill[langgraph]` pulls in a compatible `langchain-core` transitively;
  `langgraph` is never imported unless `logquill.adapters.langgraph` is
  imported explicitly.

## 0.4.0 - 2026-09-01

- Closed three gaps found auditing Phases 1–3 against their own written
  exit criteria (each had been marked "shipped" despite this):
  - Phase 1: config loading from file/env. `load_config(dict)`,
    `logger_from_file(path)` (JSON built in; YAML via the optional
    `pip install logquill[yaml]`), and `logger_from_env(prefix=
    "LOGQUILL_")` build a `Logger` from `{"name", "level", "transports":
    [{"type"|"class", "options"}], "plugins": [...]}`. A small built-in
    `"type"` registry covers the zero-dependency transports/plugins;
    anything else (every cloud/SQL/NoSQL/queue transport, the alerting
    plugins, framework adapters, your own subclass) goes through `"class"`
    — a fully-qualified dotted path, resolved the same way `logging.
    config.dictConfig` resolves one. `{prefix}LEVEL` in the environment
    always overrides a config file's level.
  - Phase 2: `FileTransport(encrypt_key=...)` encrypts each line with
    `cryptography.fernet.Fernet` before writing — worth doing here
    specifically because cloud transports typically already encrypt
    server-side, but a local log file on disk usually doesn't. Optional
    `pip install logquill[crypto]`, imported lazily.
  - Phase 3: `SyslogTransport` — RFC 5424 messages over UDP (default) or
    TCP, stdlib `socket` only, no dependency. Not a batching transport,
    unlike the HTTP-API cloud transports: syslog is one-datagram/one-
    message-per-call.
- Fixed a pre-existing crash the plugin-pipeline hypothesis property test
  caught during this audit, unrelated to the three gaps above: any of
  `Logger`'s message-taking methods (`.info()`, `.error()`, `.action()`,
  ...) raised `TypeError: got multiple values for argument 'message'` if
  the caller's `**meta` happened to contain a key literally named
  `"message"` (and `.child()`/`.span()` had the same issue with `"name"`)
  — exactly the kind of caller-crashing bug those hypothesis tests exist
  to catch. `message`/`name` are now positional-only on every affected
  method, so a `meta`/`fixed_meta` key with that exact name now flows
  through as ordinary meta instead of colliding.
- Phase 5, trace correlation & agentic tracing, complete:
  - `Logger.child(name, **fixed_meta)` — a namespaced logger sharing the
    parent's transports, with its own plugin pipeline and optional fixed
    context injected into every record.
  - `.thought()/.action()/.observation()/.decision()` — `.info()` with
    `meta.kind` pre-set, for tagging agent reasoning steps.
  - `Logger.span(name)`, used as `with agent_log.span("call_llm"):` —
    emits one record on exit carrying `meta.span_id`/`meta.duration_ms`;
    every record logged inside the block (through any method) is
    automatically stamped with `meta.parent_span_id`, so a full run
    reconstructs its exact nesting by sorting on `span_id`/`parent_span_id`.
    Still emits its record, at `ERROR` with `meta.error` set, if the block
    raises — the exception itself propagates unchanged.
  - `RunPlugin` — stamps `meta.run_id` (generated if not given) and an
    incrementing `meta.step`; one instance scopes one run, so concurrent
    runs never share a counter.
  - `TraceContextPlugin` — stamps `meta.trace_id` for cross-service
    correlation, distinct from `run_id`. Resolves an active OpenTelemetry
    span's trace id first (best-effort, lazy import), then a W3C
    `traceparent`/AWS X-Ray/GCP trace header propagated via the new
    `set_traceparent()`/`reset_traceparent()` (a `contextvars`-based
    per-thread/asyncio-task mechanism), and generates a fresh id only if
    neither is available.
  - `LogQuillAdapter` base class + `LangChainAdapter`
    (`pip install logquill[langchain]`) — maps LangChain's
    `BaseCallbackHandler` events onto the calls above; covers LangGraph
    for free, since it shares LangChain's callback system. LangChain's own
    `run_id`/`parent_run_id` are written directly onto
    `meta.span_id`/`meta.parent_span_id`. `langchain-core` is never
    imported unless `logquill.adapters.langchain` is imported explicitly.
- `CrewAIAdapter` (`pip install logquill[crewai]`) — a second
  `LogQuillAdapter` implementation, ahead of the phase schedule (CrewAI was
  listed as a Phase 5 follow-on, not required for that phase). Listens on
  CrewAI's own event bus (`BaseEventListener`) rather than a single
  callback handler; a crew kickoff and each task open/close a `.span()`,
  while agent execution, tool usage, and LLM calls become `.action()`/
  `.observation()`/`.error()` pairs with `duration_ms`. Correlation reads
  directly off CrewAI's own `event.parent_event_id`/`event.started_event_id`
  (populated by CrewAI's own `contextvars`-backed scope stack) rather than
  tracking anything independently — the same field-renaming approach
  `LangChainAdapter` takes with LangChain's `run_id`/`parent_run_id`.
  `crewai` is never imported unless `logquill.adapters.crewai` is imported
  explicitly.
- `LlamaIndexAdapter` (`pip install logquill[llamaindex]`) — a third
  `LogQuillAdapter` implementation. LlamaIndex's own instrumentation module
  splits into two cooperating registrations on a shared dispatcher, so this
  adapter holds one of each rather than being a handler itself: a span
  handler for LlamaIndex's own method-level calls (`query()`, `chat()`,
  `retrieve()`, ...), each becoming a `span_id`/`duration_ms` record with
  `parent_span_id` set for a nested call; and an event handler for named
  events fired *within* those calls, classified generically by class-name
  suffix (`*StartEvent` -> `.action()`, `*EndEvent` -> `.observation()`,
  `*ErrorEvent` -> `.error()`) rather than enumerated one by one, so a new
  LlamaIndex event type needs no adapter change to show up correctly.
  `llama-index-core` is never imported unless `logquill.adapters.llamaindex`
  is imported explicitly.
- `AutoGenAdapter` (`pip install logquill[autogen]`) — a fourth
  `LogQuillAdapter` implementation, rounding out every framework CLAUDE.md
  names as a Phase 5 follow-on. Architecturally different from the other
  three: (Microsoft) AutoGen's actual integration point is a stdlib
  `logging.Handler` attached to `autogen_core.EVENT_LOGGER_NAME`, where
  model clients and tools log structured event objects (not strings), so
  the adapter is a `Handler` whose `emit()` unpacks that object rather than
  a callback/event-bus registration. Each event becomes a flat
  `.action()`/`.observation()`/`.error()` record; unlike the other three
  adapters, AutoGen's structured events carry no call-level
  `span_id`/`parent_span_id`-equivalent (only `agent_id`), so there's no
  span tree to reconstruct here — documented as a real limitation, not
  glossed over. Covers `autogen-core`/`autogen-agentchat` only — **not
  AG2**, which forked from AutoGen and, as of its 2026 rewrite, moved onto
  its own event-driven architecture sharing none of this (confirmed against
  its source: zero references to `EVENT_LOGGER_NAME`); unlike LangGraph
  sharing LangChain's callback system, this is a genuine divergence and AG2
  would need its own adapter. `autogen-core` is never imported unless
  `logquill.adapters.autogen` is imported explicitly.

## 0.3.0 - 2026-08-31

- Plugin pipeline, Phase 4 complete: `SamplingPlugin` gained tail-based
  elevation — with `transports=` set, a record that would be dropped is
  buffered per `meta["trace_id"]` (configurable via `trace_key`) instead of
  discarded outright, and if any later record in that trace reaches
  `elevate_at` (default `ERROR`), the whole trace — every buffered record
  plus everything after — ships, flushed straight to `transports`. Buffering
  is bounded by `max_buffered_records` and `max_traces`, oldest trace
  evicted first. Without `transports`, behavior is unchanged from plain
  rate-based sampling.
- `Logger.use()` (and the `plugins=[...]` constructor list) now accepts a
  plain function alongside a `Plugin` instance — wrapped internally as an
  anonymous `Plugin` (`FunctionPlugin`) — so a one-off `before_log`-style
  transform doesn't require subclassing `Plugin` first.
- `PIIRedactPlugin`: regex-based PII redaction over `meta` **values**
  (emails, SSNs, credit-card numbers, phone numbers), recursing through
  nested dicts/lists/tuples and matching regardless of which key holds the
  value — complements `RedactPlugin`'s exact-key matching. Depth- and
  cycle-bounded, so a circular reference or pathologically deep structure
  can't hang or crash the caller. An opt-in `use_presidio=True` mode
  (`pip install logquill[presidio]`) routes values through Microsoft
  Presidio's analyzer/anonymizer instead, for ML-based detection; Presidio
  is imported lazily and stays a real, non-default dependency.
- `TamperEvidentPlugin`: hash-chains every record (`meta.hash` over the
  record's own content plus the previous record's `meta.hash`, stored as
  `meta.prev_hash`), so editing, removing, or reordering a line in a
  written log breaks the chain from that point on. Ships with a static
  `TamperEvidentPlugin.verify_chain(records)` to check a log after the
  fact. Opt-in — hashing every record has a real, measurable CPU cost.
- `AlertingPlugin` base class + `SlackAlertPlugin`, `PagerDutyAlertPlugin`,
  and `EmailAlertPlugin`: fires on ERROR/FATAL (or any configurable
  `threshold`), with the actual send always running on a background
  thread so a slow or unreachable destination can never block the log call
  that triggered it. Repeated identical errors (same level + logger +
  message by default, or a custom `dedupe_key`) within
  `dedupe_window_seconds` collapse into one follow-up alert carrying an
  occurrence count instead of spamming the destination once per record.
  `send_alert` failures are caught and routed to the plugin's own
  `on_error`, same as any other plugin hook. Tracking is bounded to
  `max_tracked_keys` concurrent dedupe windows — alerting degrades under
  extreme cardinality, logging itself never does. All three concrete
  plugins use only the stdlib (`urllib`, `smtplib`) — no new required
  dependency.
- Fixed a pre-existing gap surfaced by a new property-based test (see
  below): `Logger`'s per-transport dispatch had no error handling, so a
  transport that failed to format or write a given record (e.g.
  `JSONFormatter` on a `meta` value containing a circular reference) would
  propagate the exception straight to the caller. Now caught and logged via
  the same `logging.getLogger("logquill")` channel `BatchingTransport`
  already uses, per transport, so one broken transport can't crash the
  caller or stop other attached transports from receiving the record.
- Added a `hypothesis`-based property test (new `dev` dependency) that
  drives the plugin pipeline (`ContextPlugin`, `RedactPlugin`,
  `PIIRedactPlugin`, `TamperEvidentPlugin`) with adversarial `meta` —
  deeply nested structures, unusual scalar types, non-JSON-serializable
  values, and circular references — asserting the pipeline never crashes
  the caller, only ever fails closed.

- New transports: SQL (`BaseSQLTransport` + `SQLiteTransport`,
  `PostgresTransport`, `MySQLTransport`), NoSQL (`MongoDBTransport`,
  `DynamoDBTransport`, `RedisTransport`), message queues
  (`BaseQueueTransport` + `KafkaTransport`, `RabbitMQTransport`,
  `SQSTransport`, `PubSubTransport`), and cloud-native sinks
  (`CloudWatchTransport`, `CloudLoggingTransport`, `AppInsightsTransport`,
  `DatadogTransport`, `ElasticsearchTransport`, `NewRelicTransport`) —
  full parity with `logquill-js` 0.2.0. All of it sits on a new shared
  `BatchingTransport` base that bounds its buffer by both record count and
  estimated byte size, swaps the buffer out before sending so a
  synchronous re-entrant flush can't double-send, and catches a failing
  send rather than propagating it to the caller (logged via Python's
  stdlib `logging.getLogger("logquill")`) — a slow or down sink can't
  crash the process. Every optional backend driver (`psycopg2-binary`,
  `pymysql`, `pymongo`, `boto3`, `redis`, `kafka-python`, `pika`,
  `google-cloud-pubsub`, `google-cloud-logging`) is a lazy, injectable
  dependency behind a new `pyproject.toml` extra (`postgres`, `mysql`,
  `mongodb`, `redis`, `kafka`, `rabbitmq`, `pubsub`, `gcp-logging`, and a
  shared `aws` extra for CloudWatch/DynamoDB/SQS, all boto3-backed); a
  missing driver raises an actionable `ImportError` rather than a
  cryptic one, and every test injects a hand-written fake instead of
  requiring a live service. `SQLiteTransport` needs no extra at all
  (stdlib `sqlite3`).

  Two deliberate departures from `logquill-js`'s implementation, same
  outward behavior: `AppInsightsTransport` posts to Application Insights'
  public ingestion endpoint via stdlib `urllib` instead of an Azure SDK
  dependency, and `SQSTransport` dispatches its 10-message chunks
  sequentially rather than concurrently, since this project's dispatch is
  still fully synchronous end to end (true concurrency arrives once a
  non-blocking async worker exists). `SyslogTransport` isn't included
  here either, matching `logquill-js` 0.2.0, which didn't ship it; it's a
  shared follow-up for both packages, not a Python-only gap.

  Also restructured `logquill/transport.py`, `console_transport.py`,
  `file_transport.py`, and `http_transport.py` into a new
  `logquill/transports/` subpackage (with `sql/`, `nosql/`, `queue/`, and
  `cloud/` subpackages) to hold the 17 new transports — a pure move, the
  public `from logquill import ...` surface is unchanged.

- Plugin pipeline: `Plugin` base (`before_log`/`after_log`/`on_error`,
  all optional to override), `ContextPlugin` (merges fixed context into
  `meta`), `RedactPlugin` (replaces sensitive `meta` values by key, case-
  insensitive), and `SamplingPlugin` (probabilistically drops records).
  `Logger` now accepts `plugins=[...]` and gained `.use(plugin)` to register
  one and chain. A plugin hook that raises is caught, routed to that same
  plugin's `on_error`, and the pipeline continues — a broken plugin can't
  crash logging, verified by test.
- Added `.github/dependabot.yml`: weekly version updates for `pip`
  dependencies and GitHub Actions.
- Added GitHub issue templates: `.github/ISSUE_TEMPLATE/bug_report.yml`,
  `feature_request.yml`, and a `config.yml` that points security reports at
  private vulnerability reporting instead of a public issue.
- Added `.github/SECURITY.md`: supported-versions policy and instructions
  to report vulnerabilities via GitHub's private vulnerability reporting
  instead of public issues. Linked from the README.
- Transports: `Transport` base (`format`/`write`/`close`), `ConsoleTransport`
  (colorized, ERROR/FATAL to stderr), `FileTransport` (size-based rotation), and
  `HTTPTransport` (batched, newline-delimited JSON over stdlib `urllib`, with an
  injectable `sender` for tests or alternate backends). `Logger` now accepts
  `transports=[...]` and dispatches each record to them synchronously, and gained
  `.close()` to close all attached transports. Dispatch is still synchronous —
  a non-blocking queue/async path isn't implemented yet. Also added
  `CollectingTransport`, an in-memory transport for tests.
- Added `CODE_OF_CONDUCT.md` (Contributor Covenant v2.1), `.github/CODEOWNERS`,
  `.github/PULL_REQUEST_TEMPLATE.md`, and `CONTRIBUTING.md` documenting the
  PR workflow (branch naming, scoping, review/CI requirements, squash-merge).
- Core API: `Level` (TRACE/DEBUG/INFO/WARN/ERROR/FATAL, matching
  logquill-js's numeric weights), `parse_level()`, the `LogRecord` shape,
  `Logger` with `.trace()/.debug()/.info()/.warn()/.error()/.fatal()` and
  `.set_level()`, and a `Formatter` protocol with a `JSONFormatter`
  implementation. Log calls return the record dict (or `None` when filtered
  by level) — no transports or dispatch yet.
- Repo scaffold: `pyproject.toml`, package skeleton, dev tooling (ruff, mypy --strict, pytest), pre-commit hooks, and CI workflow.
- Packaging metadata: expanded classifiers (OS, Topic) and keywords, added an `Issues` project URL, and fixed the `Homepage`/`Repository`/`Changelog` URLs to point at the actual `nikhilvdev/logquill-python` GitHub repo instead of a stale placeholder org.
- Added a pepy.tech download-count badge to the README for tracking installs.
