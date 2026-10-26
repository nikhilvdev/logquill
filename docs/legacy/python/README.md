# logquill

[![CI](https://github.com/nikhilvdev/logquill-python/actions/workflows/ci.yml/badge.svg)](https://github.com/nikhilvdev/logquill-python/actions/workflows/ci.yml)
[![Publish](https://github.com/nikhilvdev/logquill-python/actions/workflows/release.yml/badge.svg)](https://github.com/nikhilvdev/logquill-python/actions/workflows/release.yml)
[![PyPI](https://img.shields.io/pypi/v/logquill.svg)](https://pypi.org/project/logquill/)
[![Python versions](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![License](https://img.shields.io/github/license/nikhilvdev/logquill-python)](LICENSE)
[![GitHub tag](https://img.shields.io/github/v/tag/nikhilvdev/logquill-python)](https://github.com/nikhilvdev/logquill-python/tags)
[![Downloads](https://static.pepy.tech/badge/logquill)](https://pepy.tech/project/logquill)

A structured, leveled logging framework for Python with pluggable transports.
Sibling to [`logquill` on npm](https://www.npmjs.com/package/logquill)
(`logquill-js`) — same log record shape, same level names, one mental model
across a Python + Node stack.

**License note:** starting with 2.0.0, new releases are source-available
under the [Functional Source License](#license) (FSL-1.1-MIT), not MIT.
Free for nearly everyone — internal use, non-commercial work, building a
product or service that isn't a logging framework — paid only if you're
building a competing logging/observability offering. Versions 1.0.0 and
earlier stay MIT forever; this doesn't change anything about those. See
[License](#license) below for what that actually means.

Status: stable (2.0.0). See `CHANGELOG.md` for what changed in each release
and [MIGRATING.md](MIGRATING.md) for upgrading from 1.x.

## Features

- **Structured by default** — every call carries a `meta` dict, not just a message string
- **Cross-language record shape** — identical JSON shape and level names/weights as [`logquill` on npm](https://www.npmjs.com/package/logquill)
- **Pluggable transports** — `ConsoleTransport` (colorized, stderr for errors), `FileTransport` (rotation, optional encryption-at-rest), `HTTPTransport` (batched), `SyslogTransport` (RFC 5424, UDP/TCP), plus SQL/NoSQL/message-queue/cloud-native sinks (see [Transports](#transports)); write your own by subclassing `Transport`
- **Pluggable formatters** — `JSONFormatter` (default, machine-readable), `TextFormatter` (human-readable, for terminals), and `LogfmtFormatter` (`key=value`, the Heroku/Go convention); implement `format(record) -> str` for your own — see [Formatters](#formatters)
- **Config from file/env** — `load_config(dict)`, `logger_from_file(path)` (JSON/YAML), `logger_from_env()` build a `Logger` from one config shape — see [Config](#config)
- **Plugin pipeline** — `ContextPlugin`, `RedactPlugin` (by key), `PIIRedactPlugin` (by pattern), `SamplingPlugin` (with tail-based elevation), `TamperEvidentPlugin` (hash-chained logs), `TraceContextPlugin` (cross-service trace correlation), and `AlertingPlugin` (`SlackAlertPlugin`/`PagerDutyAlertPlugin`/`EmailAlertPlugin`, deduplicated) out of the box; a broken plugin can't crash logging; `.use()` also accepts a plain function, no subclassing required (see [Plugins](#plugins))
- **Agentic & harness tracing** — `.child()` loggers, `RunPlugin`, `.thought()/.action()/.observation()/.decision()`, `with agent_log.span(...)`, and framework adapters — `LangChainAdapter` (`pip install logquill[langchain]`), `LangGraphAdapter` (`pip install logquill[langgraph]`, adds checkpoint interrupt/resume events on top), `CrewAIAdapter` (`pip install logquill[crewai]`), `LlamaIndexAdapter` (`pip install logquill[llamaindex]`), `AutoGenAdapter` (`pip install logquill[autogen]`), and `OpenAIAgentsAdapter` (`pip install logquill[openai-agents]`) — see [Agentic & harness tracing](#agentic--harness-tracing)
- **LLM calls & OpenTelemetry** — `logger.llm_call(model=, tokens_in=, tokens_out=, cost_usd=, ...)` records a first-class `llm` block, `span(capture_state=...)` records what state changed, repeated tool calls get `meta.retry_count` automatically, and `OTLPTransport` (`pip install logquill[otel]`) exports it all as real OpenTelemetry spans named and attributed per the GenAI semantic conventions; `OTelLogsTransport` sends records as OTLP logs — see [LLM calls & OpenTelemetry](#llm-calls--opentelemetry)
- **Auto-instrumentation & MCP** — `logquill.instrument.anthropic(logger)`/`.openai(logger)`/`.litellm(logger)` patch a provider SDK so every LLM call logs itself, no call-site changes; `OpenAIAgentsAdapter` covers the OpenAI Agents SDK; `logquill.tracing.mcp` propagates trace context over MCP requests and stamps `meta.mcp.*` — see [Auto-instrumentation and MCP](#auto-instrumentation-and-mcp)
- **Non-blocking async dispatch** — `Logger(async_dispatch=True)` moves transport writes onto a background thread with a bounded queue and a configurable backpressure policy (`drop_oldest`/`drop_newest`/`block`); `flush()`/`flush_async()` and a `with_lambda`/`with_cloud_function`/`with_azure_function` decorator make serverless shutdown safe — see [Async dispatch & serverless safety](#async-dispatch--serverless-safety)
- **Zero required runtime dependencies** — stdlib only; `aiohttp` (`logquill[http]`) is opt-in, for keep-alive HTTP delivery
- **Typed throughout** — `mypy --strict` clean on the public API
- **Context propagation, exception capture & the stdlib bridge** — `bind_context()` (`contextvars`-based, no manual passing), `exc_info=` on any `Logger` method (formatted traceback into `meta["stack"]`), `LogQuillHandler` (bridges stdlib `logging` into a `Logger`), and `RateLimitPlugin` — see [Context propagation, exception capture & the stdlib bridge](#context-propagation-exception-capture--the-stdlib-bridge)
- **Cheap when idle, precise when it counts** — `logger.opt(lazy=True)` defers expensive `meta` values until a record will really be emitted, `logger.opt(depth=N)` reports the right caller from inside a wrapper, `logquill.disable(__name__)` silences a library's own logs by default, and queued records are flushed automatically at interpreter exit — see [Lazy values, caller depth & disabling a library](#lazy-values-caller-depth--disabling-a-library)
- **Parse any log file** — `parse()` pulls structured fields out of a log file (LogQuill's own or a legacy format) with a regex, streaming line by line — see [Parsing log files](#parsing-log-files)
- **CLI** — `logquill tail app.log --level=warn --json -f` for filtering/following a JSONL log file in local dev, no extra install — see [CLI](#cli)
- **Local-first trace viewer** — `logquill trace <run_id> --file logs.jsonl` prints an annotated span tree (streamed, bounded memory at gigabyte scale); `logquill serve` runs a small offline web UI (stdlib only — run list, span tree/waterfall, search, level filter) reading JSONL or a `SQLiteTransport` database; `logquill dev` live-renders the current run as it happens — no account, nothing leaves your machine
- **Runtime intelligence** — `FlightRecorderPlugin` ships a run's buffered DEBUG trail only if it errors; `RunSummaryPlugin` emits one record with a run's total tokens/cost/tool calls/retries/errors; `AdaptiveSamplingPlugin` always keeps errors and slow spans while capping ordinary traffic to a bytes-per-second budget; `install_signal_level_handler`/`LevelFileWatcher`/`LevelEnvWatcher` change a logger's level without a restart — see [Runtime level control](#runtime-level-control)
- **Metrics bridge** — `MetricsPlugin` turns ERROR/FATAL records into a counter and any `meta.duration_ms` into a histogram, tagged by logger; ships with `OTelMetricsBackend` (default), `StatsDBackend`, and `PrometheusPushgatewayBackend` — see [Metrics bridge](#metrics-bridge)
- **Graph transports** — `Neo4jTransport`, `FalkorDBTransport`, `NeptuneTransport` write every record as a node and `parent_span_id` as a `PARENT_OF` edge, so a run's span tree is a Cypher traversal away with zero manual graph-writing code; one shared `BaseGraphTransport` means switching backends only means swapping the transport — see [Graph transports](#graph-transports)
- **A faster JSON backend, and free-threaded Python support** — `JSONFormatter(backend="orjson")` (`pip install logquill[fast]`) for less CPU per call under sustained volume; the worker and every batching transport lock around their shared buffers so a `Logger` used from multiple application threads is safe under both the GIL and free-threaded (PEP 703) builds — tested on 3.13t and 3.14t in CI — see [Thread safety](#thread-safety)

## Install

Requires Python 3.10 or newer (2.0 raised the floor from 3.8; see [MIGRATING.md](MIGRATING.md)).

```bash
pip install logquill
```

## Quickstart

```python
from logquill import Level, Logger

logger = Logger("app", level=Level.INFO)

record = logger.info("user signed up", user_id=42, plan="pro")
print(record)
# {'schema_version': '2.0', 'timestamp': '2026-08-27T18:04:12.345Z', 'level': 'INFO',
#  'logger': 'app', 'message': 'user signed up', 'meta': {'user_id': 42, 'plan': 'pro'}}

logger.debug("below threshold, dropped")  # -> None, filtered by level
logger.set_level("debug")
logger.debug("now visible")  # -> a record dict
```

Every log call returns the record dict (or `None` if filtered by level) —
`{"schema_version": "2.0", "timestamp": ISO8601, "level": str, "logger": str, "message": str, "meta": dict}`,
the same shape shared with [`logquill` on npm](https://www.npmjs.com/package/logquill).
Use `JSONFormatter` to serialize a record to the canonical JSON line:

```python
from logquill import JSONFormatter

print(JSONFormatter().format(record))
# '{"schema_version":"2.0","timestamp":"2026-08-27T18:04:12.345Z","level":"INFO","logger":"app","message":"user signed up","meta":{"user_id":42,"plan":"pro"}}'
```

### The record schema

The record shape is defined precisely by [`schema/record.schema.json`](schema/record.schema.json)
(JSON Schema 2020-12), the same file `logquill` on npm is tested against, with
a shared set of [golden records](schema/golden_records.json) that both
packages' test suites run. Two things to know:

- `schema_version` is on every record. `parse_record()` reads a record another
  process wrote, and accepts both 2.x records and logquill 1.x ones (which have
  no `schema_version` and come back labelled `"1.0"`). It raises `ValueError`,
  saying what to fix, for anything that isn't a LogQuill record.
- An LLM call's cost and latency have their own top-level `llm` block
  (`model`, `tokens_in`, `tokens_out`, `cost_usd`, `latency_ms`,
  `finish_reason`), not free-form `meta`; `meta.retry_count`,
  `meta.state_diff` and `meta.mcp.server`/`meta.mcp.tool` are reserved with
  fixed types.

```python
import json

from logquill import JSONFormatter, Logger, parse_record

logger = Logger("app")
line = JSONFormatter().format(logger.info("user signed up", user_id=42))

assert parse_record(json.loads(line))["schema_version"] == "2.0"

# a line written by logquill 1.x
old = '{"timestamp":"2026-01-01T00:00:00.000Z","level":"INFO","logger":"app","message":"hi","meta":{}}'
assert parse_record(json.loads(old))["schema_version"] == "1.0"
```

## Config

Build a `Logger` from a config dict, a JSON/YAML file, or the environment,
instead of wiring transports/plugins up by hand — the same shape across all
three:

```python
from logquill import load_config

logger = load_config({
    "name": "app",
    "level": "INFO",
    "transports": [{"type": "console"}],
    "plugins": [{"type": "context", "options": {"service": "api", "env": "prod"}}],
})
logger.info("ready")
```

```python
from logquill import logger_from_file

logger = logger_from_file("config.json")  # or config.yaml — needs `pip install logquill[yaml]`
```

```python
import os
from logquill import logger_from_env

os.environ["LOGQUILL_CONFIG_FILE"] = "config.json"
os.environ["LOGQUILL_LEVEL"] = "DEBUG"  # always overrides the file's level

logger = logger_from_env()  # prefix defaults to "LOGQUILL_"
```

A small built-in `"type"` registry covers the zero-dependency transports/
plugins (`console`, `file`, `http`; `context`, `redact`, `sampling`,
`trace_context`, `run`, `pii_redact`, `tamper_evident`). Anything else —
every cloud/SQL/NoSQL/queue transport, the alerting plugins, a framework
adapter, or your own subclass — goes through `"class"` instead, a
fully-qualified dotted path resolved the same way `logging.config.
dictConfig` resolves one:

```python
from logquill import load_config

logger = load_config({
    "transports": [
        {
            "class": "logquill.transports.cloud.datadog_transport.DatadogTransport",
            "options": {"api_key": "..."},
        }
    ],
})
```

## Transports

Attach transports to a `Logger` to actually write records somewhere. Each
record is dispatched to every attached transport synchronously by default;
pass `async_dispatch=True` to move that onto a background thread (see
[Async dispatch & serverless safety](#async-dispatch--serverless-safety)):

```python
from logquill import ConsoleTransport, FileTransport, HTTPTransport, Logger

logger = Logger(
    "app",
    transports=[
        ConsoleTransport(),  # stdout, ERROR/FATAL to stderr, colorized
        FileTransport("app.log", max_bytes=10 * 1024 * 1024, backup_count=5),
        HTTPTransport("https://logs.example.com/ingest", batch_size=50),
    ],
)

logger.info("user signed up", user_id=42, plan="pro")
logger.close()  # flushes the file handle and any buffered HTTP batch
```

Write your own transport by subclassing `Transport` and implementing
`write(formatted, record)`; `format(record)` and `close()` have sensible
defaults. `CollectingTransport` is a ready-made in-memory transport, handy
in your own tests:

```python
from logquill import CollectingTransport, Logger

sink = CollectingTransport()
logger = Logger("app.test", transports=[sink])

logger.info("hello")
assert sink.records[0]["message"] == "hello"
```

`HTTPTransport` sends with stdlib `urllib` by default. `backend="aiohttp"`
(`pip install logquill[http]`) sends over one reused keep-alive connection
instead, which saves a TCP/TLS handshake per batch against an HTTPS collector:

```python
from logquill import HTTPTransport

transport = HTTPTransport("https://logs.example.com/ingest", backend="aiohttp", timeout=5.0)
transport.close()  # sends anything buffered and closes the connection
```

Its buffer is bounded by both `batch_size` records and `max_bytes` of
formatted text, and a failed send is logged (naming the URL) and that batch
dropped — it never raises into the code that logged.

### Formatters

A transport renders each record with its `formatter`. `JSONFormatter` is the
default and the right choice for anything a machine reads. Two more ship for
other readers:

- `TextFormatter` — one human-readable entry per record, with any traceback
  printed on the lines below it. For terminals and local development.
- `LogfmtFormatter` — a single `key=value` line, the Heroku/Go convention,
  for tools (Loki, Splunk, `grep`) that expect it. Nested `meta` flattens to
  dotted keys, and a value with spaces or newlines is quoted, so a record is
  always exactly one line.

```python
import io

from logquill import ConsoleTransport, LogfmtFormatter, Logger, TextFormatter, parse_logfmt

text_out, logfmt_out = io.StringIO(), io.StringIO()
logger = Logger(
    "app.api",
    transports=[
        ConsoleTransport(formatter=TextFormatter(), colorize=False, stdout=text_out),
        ConsoleTransport(formatter=LogfmtFormatter(), colorize=False, stdout=logfmt_out),
    ],
)

logger.info("user signed up", user_id=42, http={"status": 201}, note="from the web form")

assert 'app.api: user signed up {"user_id":42,' in text_out.getvalue()

fields = parse_logfmt(logfmt_out.getvalue())
assert fields["level"] == "INFO"
assert fields["user_id"] == "42"
assert fields["http.status"] == "201"
assert fields["note"] == "from the web form"
```

In a config file, name the formatter as a string in the transport's
`options`: `{"type": "console", "options": {"formatter": "text"}}` (`"json"`,
`"text"` or `"logfmt"`). To write your own, implement `format(record) -> str`.

#### A faster JSON backend

`JSONFormatter(backend="orjson")` (`pip install logquill[fast]`) swaps the
stdlib `json` module for the Rust-backed `orjson` library — same compact
output, meaningfully less CPU per call under sustained logging volume.
Zero required dependencies is unaffected: the default stays `backend="json"`,
and `orjson` is only imported if you ask for it, raising an actionable
`ImportError` right away if it isn't installed, not on the first `format()`
call:

```python
from logquill import ConsoleTransport, JSONFormatter, Logger

transport = ConsoleTransport(formatter=JSONFormatter(backend="orjson"))
logger = Logger("app.api", transports=[transport])
logger.info("user signed up", user_id=42)
```

`orjson` doesn't currently support free-threaded Python builds (3.13t/3.14t)
— that's an `orjson` limitation, not a LogQuill one, so `backend="json"`
(the default) is what those interpreters use regardless.

### SQL, NoSQL, message queue, and cloud-native transports

Every transport below shares one design: records are **always batched**
(bounded by both count and estimated byte size via a shared
`BatchingTransport` base — never one write per log call), and every
optional backend driver is a **lazy, injectable dependency** — pass a
pre-built client/connection for tests or an alternate setup, or let the
transport construct one itself from the real driver on first use. A
missing driver raises an actionable `ImportError` telling you which
extra to install, the same shape every transport in this list follows.

`SQLiteTransport` needs no optional dependency at all (stdlib `sqlite3`),
so it's fully runnable as-is:

```python
from logquill import Logger, SQLiteTransport

transport = SQLiteTransport(filename="app.db", ensure_schema=True, max_records=100)
logger = Logger("app", transports=[transport])

logger.info("user signed up", user_id=42, run_id="run-1")
logger.close()  # flushes any buffered rows
```

Every other backend follows the same injection shape — here's
`MongoDBTransport` with a hand-rolled fake standing in for a real
`pymongo` collection (the same pattern every transport's own test suite
uses, so you never need a live service to test your own logging setup):

```python
from logquill import Logger, MongoDBTransport

class FakeCollection:
    def __init__(self):
        self.documents = []
    def insert_many(self, documents):
        self.documents.extend(documents)

collection = FakeCollection()
transport = MongoDBTransport(collection=collection, max_records=1)
logger = Logger("app", transports=[transport])

logger.info("user signed up", user_id=42)
assert collection.documents[0]["message"] == "user signed up"
```

Passing a real `pymongo.Collection` instead of a fake works identically —
`MongoDBTransport(uri="mongodb://localhost:27017", database="app", collection_name="logs")`
builds one lazily via the optional `pymongo` peer dependency.

**SQL** — `BaseSQLTransport` (a fixed `logs` table: `timestamp`/`level`/
`logger`/`message`/`meta`, plus `run_id`/`span_id`/`parent_span_id`/
`trace_id`, so a run or a cross-service trace can be queried directly).
`ensure_schema=True` is a dev/test convenience only — production
schema/migrations are your responsibility, same as every batching
transport below.

| Transport | Driver | Extra |
|---|---|---|
| `SQLiteTransport` | stdlib `sqlite3` | *(none)* |
| `PostgresTransport` | `psycopg2-binary` | `pip install logquill[postgres]` |
| `MySQLTransport` | `pymysql` | `pip install logquill[mysql]` |

**NoSQL**

| Transport | Driver | Extra |
|---|---|---|
| `MongoDBTransport` | `pymongo` | `pip install logquill[mongodb]` |
| `DynamoDBTransport` | `boto3` | `pip install logquill[aws]` |
| `RedisTransport` | `redis` | `pip install logquill[redis]` |

`DynamoDBTransport` partitions by `meta["run_id"]` (falling back to
`meta["trace_id"]`, then the logger name) with `timestamp` as the sort
key. `RedisTransport` writes to a Redis Stream via `XADD` — a fast local
buffer/tail, not a durable store.

**Message queues** — `BaseQueueTransport` (`topic` names the Kafka
topic / RabbitMQ queue / SQS queue URL / GCP Pub/Sub topic path).
Decouples log producers from consumers so a SIEM, an analytics pipeline,
and an alerting system can all fan out from one topic. `SQSTransport`
chunks at the API's 10-message `SendMessageBatch` cap:

```python
from logquill import Logger, SQSTransport

class FakeSQSClient:
    def __init__(self):
        self.calls = []
    def send_message_batch(self, QueueUrl, Entries):
        self.calls.append((QueueUrl, Entries))

client = FakeSQSClient()
transport = SQSTransport(
    topic="https://sqs.us-east-1.amazonaws.com/123456789012/app-logs",
    client=client,
    max_records=12,
)
logger = Logger("app", transports=[transport])
for i in range(12):
    logger.info(f"event {i}")
# chunked into two send_message_batch calls: 10 messages, then 2
```

| Transport | Driver | Extra |
|---|---|---|
| `KafkaTransport` | `kafka-python` | `pip install logquill[kafka]` |
| `RabbitMQTransport` | `pika` | `pip install logquill[rabbitmq]` |
| `SQSTransport` | `boto3` | `pip install logquill[aws]` |
| `PubSubTransport` | `google-cloud-pubsub` | `pip install logquill[pubsub]` |

**Cloud-native** — `DatadogTransport`, `ElasticsearchTransport`, and
`AppInsightsTransport` need no client SDK at all: each POSTs directly to
its provider's public ingestion endpoint via stdlib `urllib`, with an
injectable `sender` for tests:

```python
from logquill import DatadogTransport, Logger

class FakeSender:
    def __init__(self):
        self.calls = []
    def __call__(self, url, api_key, batch):
        self.calls.append((url, api_key, batch))

sender = FakeSender()
transport = DatadogTransport(api_key="dd-api-key", sender=sender, max_records=1)
logger = Logger("app", transports=[transport])

logger.info("user signed up", user_id=42)
```

| Transport | Mechanism | Extra |
|---|---|---|
| `CloudWatchTransport` | `boto3` | `pip install logquill[aws]` |
| `CloudLoggingTransport` | `google-cloud-logging` | `pip install logquill[gcp-logging]` |
| `AppInsightsTransport` | stdlib `urllib` (public ingestion endpoint) | *(none)* |
| `DatadogTransport` | stdlib `urllib` | *(none)* |
| `ElasticsearchTransport` | stdlib `urllib` (`_bulk` API) | *(none)* |
| `NewRelicTransport` | stdlib `urllib` + `gzip` | *(none)* |

`NewRelicTransport` gzips every payload, strips `meta["eventType"]` (New
Relic's reserved key), and on a `429` response reads `Retry-After` and
pauses sends until it elapses — dropping (not requeuing) any batch
flushed during that window, since New Relic blocks the rest of that
minute on a rate-limit breach anyway.

**`ElasticsearchTransport` is where Kibana support lives** — Kibana never
ingests data itself, it only visualizes what's already in Elasticsearch.
Every document carries [Elastic Common Schema](https://www.elastic.co/guide/en/ecs/current/ecs-reference.html)
fields (`@timestamp`, `log.level`, `service.name`, and, when present,
`trace.id`/`span.id`/`error.message`/`error.stack_trace`) alongside
LogQuill's own fields — ECS is permissive about extra fields, so nothing
is lost, but the *standard* fields being named correctly is what makes
Kibana's default Discover view, dashboards, and alerting rules work with
zero manual field mapping:

```python
import json
from logquill import ElasticsearchTransport, Logger

class FakeSender:
    def __init__(self):
        self.calls = []
    def __call__(self, url, batch):
        self.calls.append((url, batch))

sender = FakeSender()
transport = ElasticsearchTransport(
    url="http://localhost:9200", index="app-logs", sender=sender, max_records=1
)
logger = Logger("app", transports=[transport])

logger.error("payment failed")

_, lines = sender.calls[0]
document = json.loads(lines[1])  # lines[0] is the bulk API's index action
assert document["message"] == "payment failed"  # LogQuill's own field
assert document["log"] == {"level": "error"}    # added alongside it
```

A starter Kibana dashboard (importable via Kibana's Saved Objects UI) built
on these fields ships under [`docs/kibana/`](docs/kibana/).

`SyslogTransport` sends each record as one RFC 5424 message over UDP
(default) or TCP — stdlib `socket` only, no dependency, and not a batching
transport (syslog is one-message-per-call, unlike the HTTP-API transports
above):

```python
from logquill import Logger, SyslogTransport

transport = SyslogTransport(host="syslog.internal", port=514, app_name="app")
logger = Logger("app", transports=[transport])

logger.error("payment webhook failed")
```

### Graph transports

A run's `run_id`/`span_id`/`parent_span_id` already describe a tree — these
transports write that tree directly into a graph database instead of a flat
log, so "reconstruct this agent run's call tree" is a Cypher traversal
instead of application code that walks a JSONL file. `BaseGraphTransport`
owns the one query every backend runs — `MERGE` a `:LogRecord` node per
record, a `PARENT_OF` edge from `parent_span_id`, and a `HAS_RECORD` edge
grouping every node under a `:Session` keyed by `run_id` (or `trace_id`) —
so switching backends means swapping the transport, not rewriting anything:

```python
from logquill import Logger, Neo4jTransport, RunPlugin

class FakeSession:
    def __init__(self, calls):
        self._calls = calls
    def run(self, query, parameters):
        self._calls.append((query, parameters))
    def __enter__(self):
        return self
    def __exit__(self, *exc_info):
        pass

class FakeDriver:
    def __init__(self):
        self.calls = []
    def session(self, **kwargs):
        return FakeSession(self.calls)
    def close(self):
        pass

driver = FakeDriver()
transport = Neo4jTransport(driver=driver, max_records=10)
logger = Logger("agent", transports=[transport], plugins=[RunPlugin()])

with logger.span("run"):
    logger.action("call_tool", tool="search")
logger.close()
assert len(driver.calls) == 1  # one batched write for the whole span
```

Passing a real `neo4j.GraphDatabase.driver(uri, auth=...)` instead of a fake
works identically — `Neo4jTransport(uri="bolt://localhost:7687", auth=("neo4j", "password"))`
builds one lazily via the optional `neo4j` driver. Once written, the span
tree comes back with one traversal:

```cypher
MATCH (s:Session {key: "<run_id>"})-[:HAS_RECORD]->(n)
OPTIONAL MATCH (n)-[:PARENT_OF*0..]->(child)
RETURN n, child
```

Each driver is a lazy, injectable dependency, the same shape as the
SQL/NoSQL transports above:

| Transport | Driver | Extra |
|---|---|---|
| `Neo4jTransport` | `neo4j` | `pip install logquill[neo4j]` |
| `FalkorDBTransport` | `falkordb` | `pip install logquill[falkordb]` |
| `NeptuneTransport` | `boto3` (`neptunedata`, openCypher) | `pip install logquill[aws]` |

`KuzuTransport` was scoped for this release but deliberately not built —
[Kuzu's own repository](https://github.com/kuzudb/kuzu) was archived by its
maintainers on 2025-10-10 ("Kuzu is working on something new!"), with no
release schedule. Building against an archived database contradicts the
"verify it's still maintained before committing to it" bar every other
dependency in this project is held to, so it's cut rather than shipped
against a dead upstream — revisit only if the project resumes.

### Encryption-at-rest for file logs

`FileTransport(encrypt_key=...)` encrypts each line with
`cryptography.fernet.Fernet` before writing — a local log file usually
isn't encrypted server-side the way a cloud sink already is:

```python
from cryptography.fernet import Fernet
from logquill import FileTransport, Logger

key = Fernet.generate_key()  # store this somewhere safe — you need it to decrypt
transport = FileTransport("app.log", encrypt_key=key)
logger = Logger("app", transports=[transport])

logger.info("card charged", user_id=42)
logger.close()

# decrypt back, one Fernet token per line
fernet = Fernet(key)
with open("app.log", "rb") as f:
    for line in f:
        print(fernet.decrypt(line.strip()).decode("utf-8"))
```

Needs the optional `cryptography` dependency (`pip install
logquill[crypto]`), imported lazily — `FileTransport` has zero
dependencies as long as `encrypt_key` stays unset.

## Plugins

Plugins hook into the pipeline around each log call: `before_log(record)` can
transform a record or return `None` to drop it, `after_log(record)` runs once
it's been dispatched to every transport, and `on_error(exc, record)` catches
anything a plugin's own hooks raise — a broken plugin can't take down logging.
Records are **not** deep-copied through the pipeline — a plugin receives and
may mutate the same dict every other plugin sees; copy it yourself in
`before_log` if you need to preserve the original.

```python
from logquill import ContextPlugin, Logger, RedactPlugin, SamplingPlugin

logger = Logger("app")
logger.use(ContextPlugin(service="api", env="prod"))  # merged into every record's meta
logger.use(RedactPlugin(keys=["password", "token"]))  # replaces matching meta values
logger.use(SamplingPlugin(0.1))  # keep ~10% of records that reach this point

logger.info("login attempt", user_id=42, password="hunter2")
# meta: {'service': 'api', 'env': 'prod', 'user_id': 42, 'password': '***'}
# (unless this call was one of the ~90% sampling dropped, in which case it's None)
```

Write your own by subclassing `Plugin`; override only the hooks you need. For
a one-off transform, skip the subclass entirely — `.use()` also accepts a
plain function, wrapped internally as an anonymous `Plugin`:

```python
from logquill import Logger

def strip_ssn(record):
    record["meta"].pop("ssn", None)
    return record  # or None to drop the record

logger = Logger("app")
logger.use(strip_ssn)
logger.info("submit", ssn="123-45-6789", user_id=42)
# meta: {'user_id': 42}
```

### Tail-based sampling elevation

Plain `SamplingPlugin(rate)` drops records independently of each other. Add
`transports=` and every record's `meta["trace_id"]` (configurable via
`trace_key`) turns sampling tail-based instead: a dropped record is buffered
under its trace id rather than discarded, and if any later record in that
same trace reaches `elevate_at` (default `ERROR`), the whole trace — every
buffered record plus everything from then on — ships, flushed straight to
`transports`. A request that looked unremarkable when it started still
produces a complete trace once it turns out to have failed.

```python
from logquill import CollectingTransport, Logger, SamplingPlugin

sink = CollectingTransport()
sampling = SamplingPlugin(0.01, transports=[sink])  # keep ~1%, tail-elevate the rest
logger = Logger("app", transports=[sink], plugins=[sampling])

logger.info("received request", trace_id="req-42")   # likely dropped — held in the buffer
logger.info("queried database", trace_id="req-42")    # likely dropped — held in the buffer
logger.error("query timed out", trace_id="req-42")     # elevates the whole trace

assert [r["message"] for r in sink.records] == [
    "received request",
    "queried database",
    "query timed out",
]
```

Buffering is bounded by `max_buffered_records` and `max_traces` — the oldest
buffered trace is evicted once either limit is hit, so a single
high-cardinality or long-lived trace can't grow memory without limit.

### The flight recorder: DEBUG context, only for runs that fail

`FlightRecorderPlugin` generalizes the same idea from "a record sampling
happened to drop" to "a record below the level you actually want shipped,
period": run a logger at `DEBUG` so fine-grained context always exists, but
only pay to ship it for the runs that turn out to matter.

```python
from logquill import CollectingTransport, FlightRecorderPlugin, Logger

sink = CollectingTransport()
recorder = FlightRecorderPlugin(transports=[sink])  # ship_at=INFO, flush_at=ERROR
logger = Logger("app.agent", level="DEBUG", transports=[sink], plugins=[recorder])

# a run that finishes cleanly never ships its DEBUG trail
logger.debug("fetched 40 candidates", run_id="run-1")  # buffered, not shipped
logger.debug("filtered to top 3", run_id="run-1")  # buffered, not shipped
logger.info("run finished", run_id="run-1")  # ships on its own
assert [r["message"] for r in sink.records] == ["run finished"]

# a run that errors ships its full DEBUG trail, flushed by the error itself
logger.debug("fetched 40 candidates", run_id="run-2")  # buffered for now
logger.error("ranking model timed out", run_id="run-2")  # flushes the buffer
assert [r["message"] for r in sink.records[-2:]] == [
    "fetched 40 candidates",
    "ranking model timed out",
]
```

Bounded the same way tail-based elevation is: `max_buffered_records` and
`max_runs` cap the buffer, the oldest run evicted (and lost, not shipped)
once either is hit.

### One record per run: `RunSummaryPlugin`

Emits a single summary record — total tokens, cost, tool calls, retries,
and errors — when a run's outermost span closes, so a dashboard doesn't
need to re-walk every record to answer "how much did this run cost":

```python
from logquill import CollectingTransport, Logger, RunPlugin, RunSummaryPlugin

sink = CollectingTransport()
summary = RunSummaryPlugin(transports=[sink])
logger = Logger("app.agent", transports=[sink], plugins=[RunPlugin(), summary])

with logger.span("run", operation="invoke_agent", agent_name="planner"):
    logger.llm_call("chat", model="m", tokens_in=1200, tokens_out=340, cost_usd=0.02)
    logger.action("search", tool="search")

run_summary = sink.records[-1]
assert run_summary["message"] == "run summary"
assert run_summary["meta"]["tokens_in"] == 1200
assert run_summary["meta"]["tool_calls"] == 1
assert run_summary["meta"]["errors"] == 0
```

### Adaptive sampling with a byte-rate budget

`AdaptiveSamplingPlugin` always keeps errors and slow spans, samples
everything else, and caps how many bytes of that ordinary traffic pass
through per second — adjusting its own rate up when there's room to spare
and down when it's saturated, instead of one fixed rate tuned by hand:

```python
from logquill import AdaptiveSamplingPlugin, Logger

logger = Logger(
    "app",
    plugins=[AdaptiveSamplingPlugin(base_rate=0.1, slow_ms=1000, max_bytes_per_second=100_000)],
)

logger.error("always kept")                        # errors bypass sampling entirely
with logger.span("slow_call", duration_ms=1500):    # slow spans bypass it too
    pass
logger.info("ordinary traffic")                     # sampled at the current rate
```

### PII redaction by pattern, not just key

`RedactPlugin` redacts by exact key match. `PIIRedactPlugin` complements it by
scanning `meta` **values** — recursively through nested dicts/lists/tuples —
for emails, SSNs, credit-card numbers, and phone numbers, and redacts matches
wherever they appear, regardless of which key holds them:

```python
from logquill import Logger, PIIRedactPlugin

logger = Logger("app", plugins=[PIIRedactPlugin()])

logger.info("support ticket", notes="reach me at jane@example.com, ssn 123-45-6789")
# meta: {'notes': 'reach me at ***, ssn ***'}
```

Detection is regex-based by default — fast, dependency-free, matched on shape
rather than meaning. Beyond PII, the same scan also catches well-known
API-key/secret shapes wherever they appear — OpenAI/Anthropic keys, AWS
access key IDs, GitHub/Slack tokens, JWTs, PEM private-key blocks (the whole
block, not just the `BEGIN` line), and generic `Bearer` tokens — each a
provider's own fixed prefix or structure, chosen to keep false positives low.
For fuzzier ML-based detection instead, pass `use_presidio=True` (`pip
install logquill[presidio]`) to route values through Microsoft Presidio's
analyzer/anonymizer; Presidio stays a real, opt-in dependency, never a
default one.

`RedactPlugin` can also target a **field classification**
(`FIELD_CLASSES`: `secret` \| `pii` \| `content`) instead of
listing key names one by one:

```python
from logquill import RedactPlugin

RedactPlugin(keys=(), classes=["secret"])  # exactly today's default key set, by class
```

### Tamper-evident logs

`TamperEvidentPlugin` hash-chains every record — each one's `meta.hash` covers
its own content plus the previous record's hash — so editing, removing, or
reordering a line in a written log breaks the chain from that point on.
Opt-in, since hashing every record has a real CPU cost:

```python
from logquill import Logger, TamperEvidentPlugin

logger = Logger("app", plugins=[TamperEvidentPlugin()])
records = [logger.info(f"step {i}") for i in range(3)]

assert TamperEvidentPlugin.verify_chain(records) is True

records[1]["message"] = "tampered"  # simulate an edited log line
assert TamperEvidentPlugin.verify_chain(records) is False
```

`logquill verify <file>` wraps the same check for a JSONL file on disk,
reporting *where* and *why* it broke:

```bash
logquill verify app.log
# logquill verify: OK — 142 record(s) verified in app.log
# logquill verify: chain head is 9f1c...e03a
```

Hash-chaining alone can't catch a file being **truncated** — delete the last
few lines and what remains is still perfectly self-consistent, just shorter.
Signing the chain's current head, and keeping that signature somewhere other
than the log file itself, catches that too:

```python
from logquill import Logger, TamperEvidentPlugin, verify_signed_chain

tamper = TamperEvidentPlugin()
logger = Logger("app", plugins=[tamper])
records = [logger.info(f"step {i}") for i in range(3)]

signature = tamper.sign_head(b"a-key-you-keep-somewhere-else")  # not in this file

assert verify_signed_chain(records, key=b"a-key-you-keep-somewhere-else", signature=signature).ok

truncated = records[:-1]  # an attacker deletes the last line
result = verify_signed_chain(truncated, key=b"a-key-you-keep-somewhere-else", signature=signature)
assert result.ok is False  # caught — the chain is consistent but its head moved
```

```bash
logquill verify app.log --sign-key <hex key> --signature <hex signature>
```

### Content capture policy

Prompt/completion text, tool call arguments/results, and a span's captured
state (`meta.input_messages`, `output_messages`, `system_instructions`,
`tool_arguments`, `tool_result`, `state_diff` — the `content`-classed fields
in `FIELD_CLASSES`) are governed by a per-logger policy,
**off by default** — applied before any plugin or transport ever sees the
value, not something a plugin has to remember to attach:

```python
from logquill import Logger

logger = Logger("app.agent")  # content_policy="off" by default

record = logger.llm_call("chat", model="m", input_messages=["the user's actual prompt"])
assert "the user's actual prompt" not in str(record["meta"]["input_messages"])
```

`"hash"` replaces the value with a stable digest (the same content always
hashes the same, so two records can be confirmed to share one without either
ever reaching a transport); `"truncate"` keeps the first 200 characters;
`"full"` passes it through:

```python
from logquill import Logger

Logger("app.agent", content_policy="full")  # see it all
Logger("app.agent", content_policy="hash")  # correlate without exposing it
```

### `AuditLogger`: a preset for audit trails

`AuditLogger` bundles `RedactPlugin`, `PIIRedactPlugin`, and
`TamperEvidentPlugin`, with content capture off by default — the technical
controls an audit trail commonly needs, in one call. (Scope-honesty note,
same as the rest of the plugin pipeline: this doesn't make a deployment
"compliant" with anything by itself.)

```python
from logquill import AuditLogger

audit_log = AuditLogger("audit")  # pass transports=[FileTransport(...)] etc. as usual
record = audit_log.info("user login", user_id=42, password="hunter2")
assert record["meta"]["password"] == "***"
# .child() shares the same hash chain, not a second interleaved one

signature = audit_log.sign_head(b"a-key-you-keep-somewhere-else")
```

### Alerting on errors

`AlertingPlugin` is a base class for firing an external alert on ERROR/FATAL
(or any configurable `threshold`). It never blocks the log call that
triggered it — the actual send runs on a background thread — and repeated
identical errors within `dedupe_window_seconds` collapse into a single
follow-up alert carrying an occurrence count, instead of spamming the
destination once per record. Concrete subclasses ship for Slack, PagerDuty,
and email:

```python
from logquill import Logger, PagerDutyAlertPlugin, SlackAlertPlugin

logger = Logger(
    "app",
    plugins=[
        SlackAlertPlugin("https://hooks.slack.com/services/T000/B000/xxx"),
        PagerDutyAlertPlugin("your-events-api-v2-routing-key", threshold="FATAL"),
    ],
)

logger.error("payment webhook failed")  # posts to the Slack webhook
logger.fatal("database unreachable")  # also pages via PagerDuty (threshold=FATAL)
```

Write your own destination by subclassing `AlertingPlugin` and implementing
`send_alert(record, occurrences)`; thresholding, deduplication, and the
never-block-the-caller behavior are all handled by the base class.

`AppriseAlertPlugin` reaches everything else. It hands the alert to
[Apprise](https://github.com/caronc/apprise) (`pip install logquill[apprise]`),
which speaks to 100+ services — Discord, Telegram, Microsoft Teams, ntfy,
Matrix, SMS gateways — from one URL each, so you don't need a plugin per
service. It has the same background-thread sending, deduplication and
failure handling as the other alerting plugins. Prefer `SlackAlertPlugin` or
`PagerDutyAlertPlugin` for those two, which format richer messages than
Apprise's generic title-and-body allows:

```python
from logquill import AppriseAlertPlugin, Logger

logger = Logger(
    "app",
    plugins=[AppriseAlertPlugin(["discord://webhook_id/webhook_token", "ntfy://my-topic"])],
)
```

A URL Apprise doesn't recognize raises `ValueError` right away, at startup,
instead of silently failing on the first real alert.

### Metrics bridge

`MetricsPlugin` is the logs-to-metrics side of the three-pillars
observability model: every record at `error_at` (default `ERROR`) or above
increments an error counter tagged by `logger`, and any record carrying
`meta.duration_ms` (every span-closing record) feeds a duration histogram —
so dashboards and alerting don't have to re-derive these from raw log lines.
It runs in `after_log`, after every transport has already seen the record,
so a slow or unreachable metrics backend can never delay the log call it's
reporting on, and a raising backend is caught by the plugin pipeline the
same as any other plugin:

```python
from logquill import Logger, MetricsPlugin, RunPlugin

logger = Logger("agent", plugins=[RunPlugin(), MetricsPlugin()])

with logger.span("call_llm", duration_ms=420):
    pass
logger.error("rate limited")
# logquill.duration_ms histogram gets 420; logquill.errors counter +1, tagged logger="agent"
```

`backend` defaults to `OTelMetricsBackend()` (the OTel Metrics SDK, the same
`pip install logquill[otel]` extra `OTLPTransport` already needs, resolved
lazily so importing `logquill` never imports OpenTelemetry on its own). Pass
`StatsDBackend()` or `PrometheusPushgatewayBackend(...)` instead for a team
not yet on OTel:

```python
from logquill import Logger, MetricsPlugin, StatsDBackend

logger = Logger("app", plugins=[MetricsPlugin(StatsDBackend(host="localhost", port=8125))])
logger.error("boom")  # sends "logquill.errors:1|c|#logger:app" over UDP
```

`PrometheusPushgatewayBackend` only accumulates in-memory counters/histogram
sums on the hot path — nothing reaches the network until `.push()` (or
`.close()`, which calls it once) sends one batched `PUT` to the gateway:

```python
from logquill import Logger, MetricsPlugin, PrometheusPushgatewayBackend

class FakeSender:
    def __init__(self):
        self.calls = []
    def __call__(self, url, body):
        self.calls.append((url, body))

sender = FakeSender()
backend = PrometheusPushgatewayBackend("http://localhost:9091", job_name="app", sender=sender)
logger = Logger("app", plugins=[MetricsPlugin(backend)])

logger.error("boom")
backend.push()  # one PUT, batched
assert sender.calls[0][0] == "http://localhost:9091/metrics/job/app"
```

Implement the small `MetricsBackend` protocol (`increment`/`observe`) for
any system none of these three cover. `MetricsPlugin.close()` closes the
backend if it has a `close()` method — not called automatically by
`Logger.close()`, the same documented convention as `AlertingPlugin.close()`.

## Agentic & harness tracing

`.child()` makes a namespaced logger that shares the parent's transports —
attach run-scoped plugins to it without touching the parent's pipeline.
`RunPlugin` stamps `meta.run_id` and an incrementing `meta.step`; the
`.thought()/.action()/.observation()/.decision()` convenience methods are
`.info()` with `meta.kind` pre-set, for tagging agent reasoning steps; and
`with agent_log.span(name):` stamps `meta.span_id`/`meta.duration_ms` on
exit, with every record logged inside the block automatically getting
`meta.parent_span_id` — so a full run reconstructs its exact order and
nesting by sorting on `run_id`/`step`/`span_id`/`parent_span_id`:

```python
from logquill import CollectingTransport, Logger, RunPlugin

sink = CollectingTransport()
log = Logger("app", transports=[sink])
agent_log = log.child("agent").use(RunPlugin())

agent_log.thought("deciding what to do")
with agent_log.span("call_llm"):
    agent_log.action("call the model")
    agent_log.observation("got a response")
agent_log.decision("final answer ready")

for record in sink.records:
    print(record["meta"]["step"], record["meta"].get("kind"), record["message"])
# 0 thought deciding what to do
# 1 action call the model
# 2 observation got a response
# 3 span call_llm
# 4 decision final answer ready
```

### Cross-service trace correlation

`TraceContextPlugin` stamps `meta.trace_id` — distinct from `run_id`:
`trace_id` follows one request across services, `run_id` scopes one agent
run. It reads an active OpenTelemetry span's trace id first (if
`opentelemetry-api` is importable and a span is current), then an inbound
W3C `traceparent` / AWS X-Ray / GCP trace header — handed in via
`set_traceparent()` for the current thread/asyncio task, the way request
middleware would propagate one — and generates a fresh id only if neither
is available:

```python
from logquill import Logger, TraceContextPlugin
from logquill.plugins.trace_context_plugin import reset_traceparent, set_traceparent

# e.g. set once in HTTP middleware, from the inbound request's header
token = set_traceparent("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
try:
    logger = Logger("billing-service", plugins=[TraceContextPlugin()])
    record = logger.info("charged card")
finally:
    reset_traceparent(token)

assert record["meta"]["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
```

### Framework adapters

`LogQuillAdapter` is a thin base class for mapping a framework's own event
callbacks onto `.thought()/.action()/.observation()/.decision()` and
`.span()` — never a reimplementation of tracing logic per framework.
`LangChainAdapter` ships behind the optional `langchain` extra. LangGraph
nodes run as ordinary LangChain `Runnable`s, so it already captures node
execution with zero extra work — for LangGraph's own checkpoint
interrupt/resume events too, see [LangGraph](#langgraph) below:

```bash
pip install logquill[langchain]
```

```python
from logquill import Logger, RunPlugin
from logquill.adapters.langchain import LangChainAdapter

log = Logger("app")
handler = LangChainAdapter(log.child("agent").use(RunPlugin()))
llm = ChatOpenAI(callbacks=[handler])  # pass in like any other tracing handler
```

LangChain's own `run_id`/`parent_run_id` are written directly onto
`meta.span_id`/`meta.parent_span_id` — the shapes already match, so this is
field renaming, not translation. `langchain-core` is never imported unless
you import `logquill.adapters.langchain` yourself.

`OpenAIAgentsAdapter` maps the [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)'s
`RunHooks` the same way — pass an instance as `Runner.run(..., hooks=...)`:

```bash
pip install logquill[openai-agents]
```

```python
from agents import Agent, Runner
from logquill import Logger, RunPlugin
from logquill.adapters.openai_agents import OpenAIAgentsAdapter

log = Logger("app")
hooks = OpenAIAgentsAdapter(log.child("agent").use(RunPlugin()))
agent = Agent(name="assistant", instructions="...")
result = await Runner.run(agent, "hello", hooks=hooks)
```

Every agent activation (including a handoff's target) gets its own
`invoke_agent {agent.name}` span; `on_llm_end` becomes a real `.llm_call()`
with token usage, so `OTLPTransport` exports the whole run with cost and
token fields attached, with zero manual logging calls.

### LangGraph

LangGraph nodes execute as ordinary LangChain `Runnable`s, so
`LangChainAdapter` alone already covers everything that happens *inside* a
node — `on_chain_start`/`on_llm_start`/`on_tool_start`/etc. all fire exactly
as they would for a plain chain. What a plain `BaseCallbackHandler` can't
see is LangGraph's own checkpoint lifecycle: `on_interrupt`/`on_resume`,
fired when a graph pauses on an `interrupt()` call (e.g. for human review)
and later resumes from a persisted checkpoint — LangGraph dispatches those
two specifically to handlers that are instances of its own
`GraphCallbackHandler`, which a plain `BaseCallbackHandler` subclass never
receives. `LangGraphAdapter` is `LangChainAdapter` plus those two:

```bash
pip install logquill[langgraph]
```

```python
from logquill import Logger, RunPlugin
from logquill.adapters.langgraph import LangGraphAdapter

log = Logger("app")
handler = LangGraphAdapter(log.child("agent").use(RunPlugin()))
graph = builder.compile(checkpointer=checkpointer)
graph.invoke(input, config={"callbacks": [handler], "configurable": {"thread_id": "1"}})
```

`on_interrupt` becomes `.observation("graph_interrupted", ...)` carrying
`checkpoint_id`, `status`, `checkpoint_ns` (the subgraph namespace path, if
nested), and each pending `Interrupt`'s `id`/`value`; `on_resume` becomes
`.action("graph_resumed", ...)` with the same checkpoint fields. Both use
the event's own `run_id` as `parent_span_id`, matching the enclosing
graph's still-open chain span — the graph hasn't ended, just paused.
`pip install logquill[langgraph]` pulls in a compatible `langchain-core`
transitively, so installing it alone is enough; `langgraph` is never
imported unless you import `logquill.adapters.langgraph` yourself.

`CrewAIAdapter` ships behind the optional `crewai` extra, listening on
CrewAI's own event bus rather than a single callback handler:

```bash
pip install logquill[crewai]
```

```python
from logquill import Logger, RunPlugin
from logquill.adapters.crewai import CrewAIAdapter

log = Logger("app")
listener = CrewAIAdapter(log.child("agent").use(RunPlugin()))  # active as soon as it's constructed
crew = Crew(agents=[...], tasks=[...])
crew.kickoff()
```

A crew kickoff and each task within it open/close a `.span()`; agent
execution, tool usage, and LLM calls become `.action()`/`.observation()`/
`.error()` pairs carrying `duration_ms`. Same field-renaming approach as
`LangChainAdapter`: CrewAI's own event bus already threads
`event.parent_event_id` and, on every "ended" event, `event.started_event_id`
(the matching "started" event's id) through its own internal
`contextvars`-backed scope stack — those map directly onto
`meta.parent_span_id`/`meta.span_id`. `crewai` is never imported unless you
import `logquill.adapters.crewai` yourself.

`LlamaIndexAdapter` ships behind the optional `llamaindex` extra:

```bash
pip install logquill[llamaindex]
```

```python
from logquill import Logger, RunPlugin
from logquill.adapters.llamaindex import LlamaIndexAdapter

log = Logger("app")
adapter = LlamaIndexAdapter(log.child("agent").use(RunPlugin()))  # active as soon as it's constructed
index.as_query_engine().query("...")
```

LlamaIndex splits instrumentation into two cooperating pieces on its own
global dispatcher, so this adapter registers one of each rather than being a
handler itself: a span handler for LlamaIndex's own method-level calls
(`query()`, `chat()`, `retrieve()`, ...), which become `span_id`/
`duration_ms` records with `parent_span_id` set for a nested call (e.g.
`retrieve()` inside `query()`); and an event handler for named events fired
*within* those calls (LLM calls, retrieval, synthesis, embedding, agent
steps), classified generically by name suffix (`*StartEvent` ->
`.action()`, `*EndEvent` -> `.observation()`, `*ErrorEvent` -> `.error()`)
rather than enumerated one by one, so a new LlamaIndex event type needs no
adapter change to show up correctly. `llama-index-core` is never imported
unless you import `logquill.adapters.llamaindex` yourself.

`AutoGenAdapter` ships behind the optional `autogen` extra. Unlike the
other three, it's not a callback/event-bus registration — (Microsoft)
AutoGen's actual integration point is a stdlib `logging.Handler` attached
to `autogen_core.EVENT_LOGGER_NAME`, where model clients and tools log
structured event *objects* (not strings), so that's what this adapter is:

```bash
pip install logquill[autogen]
```

```python
from logquill import Logger, RunPlugin
from logquill.adapters.autogen import AutoGenAdapter

log = Logger("app")
adapter = AutoGenAdapter(log.child("agent").use(RunPlugin()))  # active immediately
```

Each event becomes a flat `.action()`/`.observation()`/`.error()` record
carrying whatever fields AutoGen put on it (`agent_id`, token counts, tool
name/arguments/result, ...). Worth knowing before relying on it: unlike the
other three adapters, AutoGen's structured events carry no call-level
`span_id`/`parent_span_id`-equivalent, so there's no tree to reconstruct —
just per-event correlation via `agent_id`. **Covers (Microsoft)
`autogen-core`/`autogen-agentchat` only — not AG2.** AG2 forked from
AutoGen and, as of its 2026 rewrite, moved onto its own event-driven
architecture that no longer shares `EVENT_LOGGER_NAME` or any of these
event classes; that's a real divergence, not just a detail, so it needs its
own adapter rather than reusing this one. `autogen-core` is never imported
unless you import `logquill.adapters.autogen` yourself.

## LLM calls & OpenTelemetry

**Recording an LLM call.** `logger.llm_call()` writes one record with the LLM
call's numbers in their own `llm` block — fixed names and types, so a cost or
latency dashboard can rely on them:

```python
from logquill import Logger

logger = Logger("app.agent")

record = logger.llm_call(
    "chat",
    model="example-model",
    tokens_in=1200,
    tokens_out=340,
    cost_usd=0.0123,
    latency_ms=2150.5,
    finish_reason="stop",
    provider="anthropic",  # extra keywords go in `meta`
)

assert record["llm"]["tokens_in"] == 1200
assert record["meta"] == {"kind": "action", "provider": "anthropic"}
```

Leave out what you don't have; a value of the wrong type (a negative count, a
string where a number belongs) is dropped with a warning rather than raising.
Don't put prompt or completion text in `meta` unless you mean every transport
to receive it.

**What changed during a step.** `span(name, capture_state=...)` calls your
function on entering and leaving the block and records what differs as
`meta.state_diff` — only the keys that changed, deep-copied so in-place
mutation is seen, and omitted if nothing changed. `state_diff` is a
`content`-classed field (see [Content capture policy](#content-capture-policy)),
so it's redacted by default — opt in with `content_policy="full"` (or
`"hash"`/`"truncate"`) on the `Logger`:

```python
from logquill import CollectingTransport, Logger

sink = CollectingTransport()
logger = Logger("app.agent", transports=[sink], content_policy="full")
state = {"items": 1, "user": "ada"}

with logger.span("add_item", capture_state=lambda: state):
    state["items"] = 2

assert sink.records[0]["meta"]["state_diff"] == {"before": {"items": 1}, "after": {"items": 2}}
```

Once captured, `state_diff` lands in `meta` like any other field — `PIIRedactPlugin`
scans it, and `RedactPlugin` only matches top-level keys, so don't capture secrets.

**Retries.** An `.action()` that names its tool (`tool="search"`) is tracked:
if the same call — same tool, same enclosing span, same `tool_call_id` if you
give one — is reopened before it succeeded, the record gets `meta.retry_count`
(1, 2, 3, ...). A successful `.observation(tool=...)` ends the chain, so a loop
that legitimately calls one tool many times isn't reported as retries:

```python
from logquill import Logger

logger = Logger("app.agent")

first = logger.action("look it up", tool="search")
logger.observation("timed out", tool="search", error="TimeoutError: slow")
second = logger.action("look it up", tool="search")

assert "retry_count" not in first["meta"]
assert second["meta"]["retry_count"] == 1
```

**Exporting to OpenTelemetry.** `pip install logquill[otel]`, then attach
`OTLPTransport`. It turns the records that describe work with a start and an
end into real spans, with the ids, parents and timings the records carry:

- a `span()` marked `operation="invoke_agent"` (or given an `agent_name`) is
  `invoke_agent {agent}`; any other span keeps its own name,
- an `.action()` naming its `tool` is `execute_tool {tool}`,
- a record with an `llm` block is `chat {model}` with the model, token counts
  and finish reason as the standard GenAI attributes.

```python
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from logquill import Logger, OTLPTransport, RunPlugin

exporter = InMemorySpanExporter()  # in real use: leave `span_exporter` out and set `endpoint`
logger = Logger(
    "app.agent",
    transports=[OTLPTransport(span_exporter=exporter, processor="simple")],
    plugins=[RunPlugin()],
)

with logger.span("run", operation="invoke_agent", agent_name="planner"):
    logger.llm_call("chat", model="example-model", tokens_in=1200, tokens_out=340, provider="anthropic")
    logger.action("look it up", tool="search", duration_ms=40)
logger.close()

spans = {span.name: span for span in exporter.get_finished_spans()}
assert set(spans) == {"invoke_agent planner", "chat example-model", "execute_tool search"}
chat = spans["chat example-model"]
assert chat.attributes["gen_ai.usage.input_tokens"] == 1200
assert chat.parent.span_id == spans["invoke_agent planner"].context.span_id
```

To send to a collector, give it an endpoint instead:
`OTLPTransport(endpoint="http://localhost:4318/v1/traces", service_name="my-agent")`.
Export is batched off the calling thread. Records that aren't spans are
ignored here; `OTelLogsTransport(endpoint=".../v1/logs")` sends every record
as an OTLP log record — level as severity, `meta` as attributes, and the trace
and span ids of the span it belongs to (a `span()` record its own span, any other
record the span it was logged inside), so a collector can join a log line to its
span. The span and trace ids are derived the same way in the npm package
`logquill`, so a trace that crosses a Python and a Node service lines up.

Three things worth knowing:

- **The attribute names are pinned to one release of the GenAI conventions**
  (semantic-conventions 1.44.0) and live in a single file,
  `logquill/semconv.py`. Those conventions are still marked *Development*
  upstream and have already renamed attributes (`gen_ai.system` became
  `gen_ai.provider.name`), so a future release is an edit to that file.
  `semconv_version="legacy"` selects the older provider and token-count names
  for a backend that hasn't caught up; `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`
  selects the latest. Old and new names are never emitted together.
- **Prompt and completion text is never exported by default.** It's only
  included — from `meta.input_messages` / `meta.output_messages`, as JSON — if
  you pass `capture_content=True` or set
  `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`.
- Give records a `run_id` (`RunPlugin`) or a `trace_id` (`TraceContextPlugin`)
  and one run is one trace. Without either, spans that name a parent still share
  a trace with their siblings, but not with their grandparents.

## Auto-instrumentation and MCP

**Auto-instrumentation.** `logquill.instrument.<provider>(logger)` patches a
provider SDK so every LLM call it makes anywhere in the process logs itself —
no call-site changes, and no code path forgets to log. Each provider lives
behind its own extra and is imported lazily; `import logquill.instrument`
never imports a provider SDK:

```bash
pip install logquill[instrument-anthropic]   # or [instrument-openai] / [instrument-litellm]
```

```python
import logquill.instrument as instrument
from logquill import Logger

logger = Logger("app")
instrument.anthropic(logger)

# unchanged call site — no logger, no llm_call(), nothing added here
response = client.messages.create(model="...", max_tokens=100, messages=[...])

instrument.anthropic.uninstrument()  # each instrumenter has a matching uninstrument()
```

`logquill.instrument.litellm` is the broadest one: since litellm itself fans
out to 100+ providers behind one interface, instrumenting it covers all of
them from this one call. Calling an instrumenter twice without an
intervening `.uninstrument()` raises, so a call is never wrapped twice.
**Streaming calls are a known, documented gap** in this release — a
`stream=True` call passes through untouched rather than being partially
instrumented; see each provider module's docstring
(`logquill.instrument.anthropic`, `.openai`, `.litellm`).

**MCP (Model Context Protocol).** `logquill.tracing.mcp` propagates trace context
over an MCP request and stamps `meta.mcp.*` on records logged while handling
one — with no dependency on the `mcp` package itself, since both sides just
build or read a plain dict (exactly what an MCP client's `meta=` argument and
a server's inbound `_meta` are):

```python
from logquill import Logger, TraceContextPlugin
from logquill.tracing.mcp import inbound, propagate

# client, before calling a tool:
await session.call_tool("search", {"q": "..."}, meta=propagate(run_id=agent_run_id))

# server, inside the tool handler:
server_log = Logger("mcp.files", plugins=[TraceContextPlugin()])
with inbound(ctx.request_context.meta, server="files", tool="search"):
    server_log.info("handling search")  # meta.trace_id matches the client's own
                                          # call, and meta.mcp = {"server": "files",
                                          # "tool": "search", "run_id": agent_run_id}
```

`propagate()`'s keys are namespaced under `logquill/`, an unreserved `_meta`
prefix per the MCP spec (reserved prefixes contain a
`modelcontextprotocol`/`mcp` label). A client's `run_id` never overrides the
server's own `RunPlugin` run id — it rides along informationally, under
`meta.mcp.run_id`, since the server handling a tool call is its own run, not
a continuation of the client's.

## Async dispatch & serverless safety

By default, every log call dispatches to its transports synchronously — a
slow or down sink adds latency directly to the call that triggered it. Pass
`async_dispatch=True` to move dispatch (the transport writes, plus the
`after_log` plugin hooks that follow them) onto a background thread instead,
so `.info()`/`.error()`/... return as soon as `before_log` plugin hooks have
run, without waiting on any transport's I/O:

```python
from logquill import ConsoleTransport, HTTPTransport, Logger

logger = Logger(
    "app",
    transports=[ConsoleTransport(), HTTPTransport("https://logs.example.com/ingest")],
    async_dispatch=True,
    max_queue_size=10_000,   # bounds memory if a transport stalls
    backpressure="drop_oldest",  # or "drop_newest" / "block"
)
```

`max_queue_size` bounds how many not-yet-dispatched records can pile up in
memory if a transport stalls (a down HTTP endpoint, a full disk). Once that
bound is hit, `backpressure` decides what happens next — `"drop_oldest"`
(default) evicts the oldest queued record to make room, `"drop_newest"`
discards the record that just triggered the overflow, and `"block"` makes
the calling thread wait for space instead of dropping anything. Either drop
policy logs at most one warning per minute while actively dropping, not one
per dropped record. `Logger.child()` shares its parent's queue/background
thread rather than starting a second one.

Call `logger.close()` on ordinary process shutdown — it drains any records
still queued (up to an optional `timeout`, default 5 seconds) and then
closes every transport:

```python
logger.close(timeout=5.0)
```

If a script ends without calling `close()`, LogQuill does it for you: by
default every `Logger` registers an `atexit` hook that drains the queue
(waiting up to 5 seconds) and closes its transports once, so the last records
and a batching transport's unsent batch aren't lost. Pass
`flush_at_exit=False` (or `"flush_at_exit": false` in a config file) if you'd
rather manage shutdown yourself. `atexit` only runs on a normal exit — end of
script, `sys.exit()`, an unhandled exception — not when the process is killed
outright (`SIGKILL`, `os._exit()`, or `SIGTERM` with no handler), which is
why the Kubernetes note below still applies.

For code that keeps running afterward (a request handler, a serverless
invocation), use `logger.flush()` instead — it drains the queue and flushes
each transport's own internal buffer (see `BatchingTransport`) *without*
closing anything, so the logger is still usable right after:

```python
logger.flush(timeout=2.0)          # sync callers
await logger.flush_async(timeout=2.0)  # async callers — awaits instead of blocking
```

### Serverless: flush before the container freezes

A serverless execution environment (AWS Lambda, GCP Cloud Functions, Azure
Functions) can freeze or tear down immediately after your handler returns —
a record still sitting in the async queue at that instant may never reach
its transport. `with_lambda` wraps a handler so `flush()`/`flush_async()`
happens automatically, on both a normal return and an exception, before
control goes back to the platform:

```python
from logquill import ConsoleTransport, Logger, with_lambda

logger = Logger("app", transports=[ConsoleTransport()], async_dispatch=True)


@with_lambda(logger)
def handler(event, context):
    logger.info("processing request", request_id=event["requestId"])
    return {"statusCode": 200}
```

It flushes, never closes — a warm container reuses the same `Logger`/
transports on its next invocation, and `close()` would release resources
(an open file handle, a pooled connection) that invocation needs. Works
with `async def` handlers too, and accepts a list of loggers if a handler
logs through more than one. `with_cloud_function`/`with_azure_function` are
the same decorator under a name that reads naturally at each platform's own
handler definition — the flush-before-return behavior is identical across
all three.

### Kubernetes

Default to `ConsoleTransport`, not `FileTransport`, for anything running in a
container. A container's filesystem is ephemeral and invisible to the rest of
the cluster — a log file written inside it disappears the moment the pod is
rescheduled, and nothing aggregates it in the meantime unless you also run a
sidecar to tail it back out. Writing to stdout/stderr instead costs nothing
extra: every major container runtime already captures both streams, and the
node-level log agent your cluster runs (Fluentd, Fluent Bit, Vector, or your
cloud provider's own) ships them to your aggregator without any code on your
side that needs to know that agent exists:

```python
from logquill import ConsoleTransport, Logger

logger = Logger("app", transports=[ConsoleTransport()])
```

If you do reach for `async_dispatch=True` in a container, make sure
`logger.close()` (or `logger.flush()`) runs before the container actually
stops — Kubernetes sends `SIGTERM` and then kills the process after
`terminationGracePeriodSeconds` (30s by default) regardless of whether it's
finished shutting down, so a queued-but-undispatched record can be lost if
nothing catches the signal. A `SIGTERM` handler or `preStop` hook that calls
`logger.close(timeout=...)` closes that gap the same way `with_lambda` closes
it for a serverless freeze.

### Thread safety

A single `Logger` (and the transports/plugins it was built with) is safe to
call from multiple application threads at once, with `async_dispatch=True`
or without it:

- With `async_dispatch=False` (the default), every `.info()`/`.error()`/...
  call dispatches to transports on the caller's own thread — so if several
  threads share one `Logger`, they can genuinely run a transport's `write()`
  concurrently. `BatchingTransport` (the shared base for the SQL/NoSQL/
  queue/cloud transports), `HTTPTransport`, and `FileTransport` each lock
  around their internal buffer/file-handle mutation for exactly this reason.
- With `async_dispatch=True`, every transport write and `after_log` hook
  already runs serialized on the single `AsyncWorker` background thread
  (itself built on a `threading.Condition`-guarded queue), so this is moot
  for those two things specifically — but `before_log` plugin hooks still
  run on the calling thread either way (they can filter/transform the
  record, so a later hook needs to see the result in order), which matters
  for the next point regardless of `async_dispatch`.
- A plugin with its own internal counter — `RunPlugin`'s step counter,
  `SamplingPlugin`/`AdaptiveSamplingPlugin`'s rate state,
  `TamperEvidentPlugin`'s hash chain — mutates that state in `before_log`,
  always on the calling thread. Sharing one plugin instance (via one
  `Logger`, or via `.child()`) across multiple threads that log
  concurrently isn't guarded by a lock today; `TamperEvidentPlugin`
  specifically depends on its hash chain being appended to in the same
  order records are meant to chain in, so a multi-threaded audit logger is
  the case to be most careful with. Give each thread (or each request) its
  own `.child()` logger if you need per-thread isolation, which is also the
  simpler mental model regardless of this caveat.

Verified under both the GIL and free-threaded (PEP 703, `python3.13t`/
`python3.14t`) builds in CI: the full test suite and the memory-budget
benchmarks (see [Development](#development)) run on both, plus a dedicated
burst test hammering one `BatchingTransport` from 16 threads at once and
checking every record arrived exactly once.

## Context propagation, exception capture & the stdlib bridge

`bind_context()` binds request-scoped values for a `with` block — every
`Logger` call underneath it, through any method and any number of function
calls deep, picks them up in `meta` automatically, without threading them
through every function signature by hand:

```python
from logquill import Logger, bind_context

logger = Logger("app")

def process():
    return logger.info("processing")  # no request_id passed in — picked up from context

with bind_context(request_id="req-42"):
    record = process()

assert record["meta"] == {"request_id": "req-42"}
```

It's backed by a `contextvars.ContextVar`, so concurrent asyncio tasks and
threads each see their own bound context; nested `bind_context` blocks
merge, and an explicit call-site value still wins over anything bound this
way — the same override rule `ContextPlugin` uses.

Pass `exc_info=` (an exception instance, `True` for the exception currently
being handled, or an explicit `(type, value, traceback)` tuple — the same
shapes stdlib `logging` accepts) to any `Logger` method to capture a
formatted traceback into `meta["stack"]`:

```python
from logquill import Logger

logger = Logger("app")

try:
    1 / 0
except ZeroDivisionError as exc:
    record = logger.error("payment failed", exc_info=exc, order_id=42)

assert record["meta"]["order_id"] == 42
assert "ZeroDivisionError" in record["meta"]["stack"]
```

### Local variables in tracebacks: `diagnose`

`diagnose=True` adds each frame's local variable values under its source line,
which turns "it failed in `charge()`" into "it failed because `amount` was
`0`":

```python
from logquill import Logger

logger = Logger("app")


def charge(amount):
    fee = 2.5
    return fee / amount


try:
    charge(0)
except ZeroDivisionError:
    record = logger.error("payment failed", diagnose=True)  # implies exc_info=True

assert "amount = 0" in record["meta"]["stack"]
assert "fee = 2.5" in record["meta"]["stack"]
```

**It's off by default, and it can leak sensitive data.** Whatever a local
variable holds — a password, a token, a whole request body — is written into
the log. Keep it off in production. If you do turn it on there, register
`RedactPlugin` and/or `PIIRedactPlugin`: every captured value is passed
through them *before* the traceback is formatted, so a local named `password`
or holding an email address is masked instead of printed. They only mask what
they're configured to recognize (a local's name, or a PII pattern in its
value) — a secret hiding inside a dict called `payload` is not caught. A
plugin whose redaction hook fails masks the value rather than showing it.

```python
from logquill import Logger, RedactPlugin

logger = Logger("app", plugins=[RedactPlugin()])


def login(user, password):
    raise PermissionError("bad credentials")


try:
    password = "hunter2"  # e.g. from a submitted form
    login("ada", password)
except PermissionError:
    record = logger.error("login failed", diagnose=True)

assert "user = 'ada'" in record["meta"]["stack"]
assert "hunter2" not in record["meta"]["stack"]
assert "password = ***" in record["meta"]["stack"]
```

Note what this doesn't catch: Python 3.11+ tracebacks echo the *source line*
itself (`login("ada", password)`), not just captured locals — so a secret
passed as a literal at the call site (`login("ada", "hunter2")`) would still
appear verbatim in that echoed line, since there's no variable name there for
`redact_local` to match against. Keep secrets in a named variable rather than
inline at the call site, and `diagnose=True` output genuinely has nothing left
to leak.

`LogQuillHandler` bridges stdlib `logging` calls — including from
third-party libraries you don't control — into a `Logger`, so they flow
through the same transports and plugins instead of needing every call site
rewritten:

```python
import logging
from logquill import Logger, LogQuillHandler

logger = Logger("app")
logging.getLogger().addHandler(LogQuillHandler(logger))

logging.getLogger("some.library").warning("retrying", extra={"attempt": 2})
# -> flows through `logger`'s transports as a WARN record with meta: {'attempt': 2}
```

`RateLimitPlugin` caps how many records with the same `(logger, level)` (or
a custom `key_func`) pass through per rolling window — for a noisy retry
loop that would otherwise flood a transport:

```python
from logquill import Logger, RateLimitPlugin

logger = Logger("app", plugins=[RateLimitPlugin(max_records=5, per_seconds=60)])

for _ in range(100):
    logger.error("connection refused")  # only the first 5 per minute ship
```

## Lazy values, caller depth & disabling a library

**Lazy values.** A `DEBUG`/`TRACE` call left in production code still builds
its arguments before the logger discards it. `logger.opt(lazy=True)` defers
any callable `meta` value until the record is really going to be emitted, so
a filtered call costs nothing. (If a callable raises, the record carries a
placeholder naming the error; the exception never reaches your code.)

```python
from logquill import Logger

logger = Logger("app", level="INFO")
calls = []


def expensive_dump():
    calls.append(1)
    return {"rows": 100_000}


logger.opt(lazy=True).debug("state", dump=expensive_dump)  # filtered: never called
assert calls == []

record = logger.opt(lazy=True).info("state", dump=expensive_dump)  # emitted: called once
assert calls == [1]
assert record["meta"]["dump"] == {"rows": 100_000}
```

**Caller depth.** `logger.opt(depth=N)` adds `meta.caller` (`module`,
`function`, `line`, `file`) naming the code that logged, `N` frames up from the
direct caller. Use it in a wrapper or decorator so the record points at the
wrapper's caller instead of the wrapper itself:

```python
from logquill import Logger

logger = Logger("app")


def audit(message):
    return logger.opt(depth=1).info(message)  # report audit()'s caller, not audit()


def transfer_funds():
    return audit("funds transferred")


record = transfer_funds()
assert record["meta"]["caller"]["function"] == "transfer_funds"
```

**Disabling a library.** When a library uses LogQuill internally, it should
be silent in its host application by default. The library calls
`logquill.disable(__name__)` once at import; the application can opt back in
with `enable()`. Rules cover a logger and everything nested under it, and the
most specific rule wins:

```python
import logquill
from logquill import Logger

library_log = Logger("mylib.http")  # what a library `mylib` would create

logquill.disable("mylib")
assert library_log.info("hidden") is None

logquill.enable("mylib.http")  # the app wants just this part back
assert library_log.info("visible") is not None
```

## Parsing log files

`parse()` extracts structured fields from a log file with a regex — including
logs LogQuill didn't write, like a legacy app's or a third-party tool's. It
yields a dict of the pattern's named groups for every matching line and skips
the rest. It reads one line at a time, so a multi-gigabyte file costs no more
memory than a small one. `cast` converts groups as they're read:

```python
import tempfile
from pathlib import Path

from logquill import parse

with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "legacy.log"
    path.write_text(
        "2026-01-01 10:00:00 [INFO] 200 started\n"
        "not a log line\n"
        "2026-01-01 10:00:05 [ERROR] 503 upstream down\n"
    )

    pattern = r"(?P<when>\S+ \S+) \[(?P<level>[A-Z]+)\] (?P<code>\d+) (?P<message>.*)"
    errors = [e for e in parse(path, pattern, cast={"code": int}) if e["level"] == "ERROR"]

assert errors == [
    {"when": "2026-01-01 10:00:05", "level": "ERROR", "code": 503, "message": "upstream down"}
]
```

To read back what `TextFormatter` wrote, use the ready-made
`TEXT_LOG_PATTERN` with `cast=TEXT_LOG_CASTS` (which decodes `meta` from JSON);
for `LogfmtFormatter` output, `parse_logfmt(line)` returns a dict of strings.

## Runtime level control

Change a `Logger`'s level without restarting the process — three
independent mechanisms; pick whichever fits your deployment:

```python
import os, signal
from logquill import Logger, install_signal_level_handler

logger = Logger("app")
install_signal_level_handler(logger, env_var="APP_LOG_LEVEL")
# kill -USR1 <pid> now re-reads APP_LOG_LEVEL and applies it
```

```python
from logquill import Logger, LevelFileWatcher

logger = Logger("app")
watcher = LevelFileWatcher(logger, "/var/run/app.level", poll_interval=2.0)
watcher.start()  # `echo DEBUG > /var/run/app.level` to turn on DEBUG, no restart
# watcher.stop() on shutdown
```

`LevelEnvWatcher(logger, "APP_LOG_LEVEL")` polls an environment variable the
same way — but only helps when something *inside* this same process is the
one changing it (a config-reload callback, `python-dotenv`'s
`override=True`); an OS-level environment change made from outside the
process, the way a shell normally would, is never visible to code already
running inside it. Use the signal or file mechanism for anything triggered
externally.

## CLI

Installing `logquill` also installs a `logquill` command for local
development — no extra dependencies, since it only reads the JSONL files any
`FileTransport`/`ConsoleTransport` already writes:

```bash
# print every record in the file, human-readable
logquill tail app.log

# only WARN and above, as raw JSON lines
logquill tail app.log --level=warn --json

# only the last 20 matching records
logquill tail app.log -n 20

# keep watching the file and print new records as they're appended, like `tail -f`
logquill tail app.log -f
```

Human-readable output is colorized by level (matching `ConsoleTransport`'s
colors) when writing to a terminal; pass `--no-color` to disable that, or
`--json` to print each matching record as a single JSON line instead. A line
that isn't valid JSON, or isn't a JSON object, is skipped with a warning on
stderr rather than aborting the whole tail.

### `logquill trace` — one run's span tree, from the command line

`logquill trace <run_id> --file logs.jsonl` reconstructs and prints one
agent run's span tree, annotated with each span's own duration and the
token/cost totals of everything nested under it — everything a hosted trace
UI shows you, from a plain JSONL file, no account or backend:

```bash
logquill trace run-4f2a --file logs.jsonl
```

```text
└─ [INFO] run  (812.5ms, 1540→412 tok, $0.0187)
   ├─ [INFO] plan the work
   ├─ [INFO] step  (250.0ms, 1200→340 tok, $0.0123)
   │  └─ [INFO] chat  (1200→340 tok, $0.0123)
   └─ [INFO] look it up  (40.0ms)
```

It **streams** the file line by line — reconstructing one run out of a
multi-gigabyte log file costs memory proportional to that run, not the file
(tested at gigabyte scale in `benchmarks/test_trace_memory.py`). `--json`
prints the same tree as nested `{record, rollup, children}` objects instead,
for feeding into another tool.

### `logquill serve` — a local, offline trace viewer

`logquill serve --file logs.jsonl` runs a small web UI, entirely on the
stdlib (`http.server`) — no new dependency, no account, and nothing leaves
your machine:

```bash
logquill serve --file logs.jsonl
# logquill serve: 12 run(s) found in logs.jsonl
# logquill serve: listening on http://127.0.0.1:52341/ — Ctrl+C to stop
```

Open the printed URL: a run list on the left (record count, token/cost
totals, an error badge), and a combined span-tree/waterfall view for
whichever run you click — indentation shows nesting, bar position and width
show timing. Search and the level filter both work inside a selected run
(client-side, instant) and, with nothing selected, across every run at once
(via `/api/search`, still streamed rather than loaded into memory).

`--db logs.sqlite` reads from a `SQLiteTransport`-written database instead of
a JSONL file. One limitation, inherent to that transport's fixed table
schema: it doesn't store the `llm` block, so runs served from SQLite show no
token/cost annotations even if the original records had them — trace from
the JSONL file (or a transport that does keep `llm`) to see those.

`logquill serve` computes the run list once at startup by streaming through
the source; it's a snapshot, not a live tail — restart it to pick up runs
logged after it started.

### `logquill dev` — watch an agent run live

`logquill dev logs.jsonl` follows a log file like `tail -f`, but redraws the
current run's span tree — colorized, screen cleared between redraws on a
terminal — every time a new record for it arrives, instead of printing flat
lines:

```bash
logquill dev logs.jsonl
```

It tracks whichever run's records have arrived most recently by default, so
the view follows an agent from one run to the next without restarting; pass
`--run-id` to pin it to one run instead. `--backlog N` (default 5000) caps
how much of the file's *existing* content seeds the very first render, so
pointing it at a large pre-existing file doesn't stall before the first draw
— once running, though, a `dev` session keeps its own growing record list in
memory for as long as it runs, unlike `trace`'s bounded streaming.

## API reference

Every public class and function is documented with a docstring; the full
reference, generated from those docstrings with [pdoc](https://pdoc.dev), is
published at
[nikhilvdev.github.io/logquill-python](https://nikhilvdev.github.io/logquill-python/)
and rebuilt on every push to `main`. To build it locally:

```bash
pip install -e ".[docs]"
pdoc --docformat google logquill  # opens a local server; add -o DIR to write static HTML instead
```

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,http,hooks]"
pre-commit install

ruff check .
mypy logquill
pytest
pytest benchmarks  # memory budgets for the logging hot path
```

CI also runs the full suite and the benchmarks on free-threaded Python
(`3.13t`/`3.14t`) — see [Thread safety](#thread-safety). `hypothesis` and
`orjson` (the `fast` extra) don't currently build for `3.13t`, an upstream
PyO3/orjson limitation, not a LogQuill one, so that job installs a smaller
dependency set there; `tests/conftest.py` turns a missing `hypothesis` into
a clean skip of the property-based suites rather than a collection error.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the PR workflow, the
[Code of Conduct](CODE_OF_CONDUCT.md) for community standards, and
[SECURITY.md](.github/SECURITY.md) for how to report a vulnerability.

## License

**Versions 1.0.0 and earlier are MIT**, full stop — already published, and
relicensing later code doesn't and can't change the terms of what's already
out there. If you depend on one of those versions, nothing here affects you.

**Version 2.0.0 and later are [FSL-1.1-MIT](LICENSE)** (the [Functional
Source License](https://fsl.software), MIT Future variant). In plain terms:

- You can use, modify, self-host, and redistribute logquill for **almost
  anything** — your own app or service, internal tooling, a client project,
  non-commercial education or research, or as part of professional services
  you provide to someone else — with no payment or extra permission needed.
- The one thing you can't do without a separate commercial license is use
  logquill to build a **competing product or service**: one that substitutes
  for logquill itself, substitutes for another product/service built on
  logquill, or offers substantially the same functionality. That's aimed at
  "take this, repackage it, sell it as your own logging/observability
  product" — not at ordinary use of a logging library inside whatever you're
  actually building.
- **Each version converts to plain MIT two years after that version's own
  release date.** So 2.0.0 becomes MIT two years after 2.0.0 ships,
  independent of whatever the latest version is by then — the restriction
  only ever applies to recent releases, never indefinitely.
- Redistributing logquill (modified or not) requires keeping the copyright
  notice and a copy of (or link to) these terms — that's the "credit"
  obligation the license itself carries.

Need a commercial license (or just have questions about whether your use
case needs one)? Open an issue, or reach out to the author directly.

This is a source-available license, not an OSI-approved open-source one —
drafted from the official FSL template and not customized beyond filling in
the licensor/year, but also not reviewed by a lawyer on either side of a
specific deal. If a commercial license matters to your business, have your
own counsel look at the exact terms before you rely on them.
