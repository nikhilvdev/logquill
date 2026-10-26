# Public API names, per language

Every binding exposes the same operations under the casing that is idiomatic for its
language. A binding is "at parity" (docs/spec/09-parity-and-gap-closing.md, section D) when
every row below exists in it. The behaviour of each row is specified in `docs/spec/`; this
file only fixes the names.

## Casing

| Language | Types | Functions and methods | Constants | Notes |
|---|---|---|---|---|
| Rust | `PascalCase` | `snake_case` | `SCREAMING_SNAKE` | |
| Python | `PascalCase` | `snake_case` | `SCREAMING_SNAKE` | `meta` passed as keyword arguments |
| Node / TypeScript, WASM | `PascalCase` | `camelCase` | `SCREAMING_SNAKE` | `meta` passed as an object; `Level` is an enum |
| Go | `PascalCase` (exported) | `PascalCase` | `PascalCase` | `meta` passed as `...Field` or a map |
| Kotlin / Java | `PascalCase` | `camelCase` | `SCREAMING_SNAKE` | |
| Swift | `PascalCase` | `camelCase` | `camelCase` | |
| C# | `PascalCase` | `PascalCase` | `PascalCase` | |
| Ruby | `PascalCase` | `snake_case` | `SCREAMING_SNAKE` | |

The wire format (record fields and `meta` keys) is `snake_case` in every language.

## Logger

| Concept (spec) | Rust | Python | Node / TS | Go | Kotlin | Swift | C# | Ruby |
|---|---|---|---|---|---|---|---|---|
| Logger type | `Logger` | `Logger` | `Logger` | `Logger` | `Logger` | `Logger` | `Logger` | `Logger` |
| Level type | `Level` | `Level` | `Level` | `Level` | `Level` | `Level` | `Level` | `Level` |
| Log at a level | `trace` `debug` `info` `warn` `error` `fatal` | same | same | `Trace` `Debug` `Info` `Warn` `Error` `Fatal` | same as Node | same as Node | `Trace` `Debug` `Info` `Warn` `Error` `Fatal` | same as Python |
| Agent kinds | `thought` `action` `observation` `decision` | same | same | `Thought` `Action` `Observation` `Decision` | same as Node | same as Node | `Thought` `Action` `Observation` `Decision` | same as Python |
| LLM call | `llm_call` | `llm_call` | `llmCall` | `LLMCall` | `llmCall` | `llmCall` | `LlmCall` | `llm_call` |
| Span | `span` | `span` | `span` | `Span` | `span` | `span` | `Span` | `span` |
| Child logger | `child` | `child` | `child` | `Child` | `child` | `child` | `Child` | `child` |
| Add a plugin | `use_plugin` | `use` | `use` | `Use` | `use` | `use` | `Use` | `use` |
| Set level | `set_level` | `set_level` | `setLevel` | `SetLevel` | `setLevel` | `setLevel` | `SetLevel` | `set_level` |
| Flush | `flush` | `flush` / `flush_async` | `flush` | `Flush` | `flush` | `flush` | `Flush` / `FlushAsync` | `flush` |
| Close | `close` | `close` | `close` | `Close` | `close` | `close` | `Close` / `Dispose` | `close` |
| Per-call options (lazy, caller depth) | `opt` | `opt` | `opt` | `Opt` | `opt` | `opt` | `Opt` | `opt` |

## Context, toggles, parsing

| Concept | Rust | Python | Node / TS | Go | Kotlin | Swift | C# | Ruby |
|---|---|---|---|---|---|---|---|---|
| Bind request context | `bind_context` | `bind_context` | `bindContext` | `BindContext` | `bindContext` | `bindContext` | `BindContext` | `bind_context` |
| Current context | `current_context` | `current_context` | `currentContext` | `CurrentContext` | `currentContext` | `currentContext` | `CurrentContext` | `current_context` |
| Disable a logger tree | `disable` | `disable` | `disable` | `Disable` | `disable` | `disable` | `Disable` | `disable` |
| Enable a logger tree | `enable` | `enable` | `enable` | `Enable` | `enable` | `enable` | `Enable` | `enable` |
| Read a record | `parse_record` | `parse_record` | `parseRecord` | `ParseRecord` | `parseRecord` | `parseRecord` | `ParseRecord` | `parse_record` |
| Parse a logfmt line | `parse_logfmt` | `parse_logfmt` | `parseLogfmt` | `ParseLogfmt` | `parseLogfmt` | `parseLogfmt` | `ParseLogfmt` | `parse_logfmt` |
| Extract fields with a regex | host-side | `parse` | `parse` | `Parse` | `parse` | `parse` | `Parse` | `parse` |
| Logger from config | `Logger::from_config` | `logger_from_file` / `logger_from_env` / `load_config` | `loggerFromFile` / `loggerFromEnv` / `loadConfig` | `FromFile` / `FromEnv` / `LoadConfig` | `fromFile` / `fromEnv` / `loadConfig` | `fromFile` / `fromEnv` / `loadConfig` | `FromFile` / `FromEnv` / `LoadConfig` | `from_file` / `from_env` / `load_config` |

## Plugins (class names are `PascalCase` everywhere)

`ContextPlugin`, `RunPlugin`, `RateLimitPlugin`, `SamplingPlugin`, `AdaptiveSamplingPlugin`,
`FlightRecorderPlugin`, `RunSummaryPlugin`, `TraceContextPlugin`, `RedactPlugin`,
`PIIRedactPlugin`, `TamperEvidentPlugin`, `AlertingPlugin`, `SlackAlertPlugin`,
`PagerDutyAlertPlugin`, `EmailAlertPlugin`, `AppriseAlertPlugin` (where the library exists),
`MetricsPlugin`, `OtelSpanProcessor`.

| Concept | Rust | Python | Node / TS | Go | Kotlin | Swift | C# | Ruby |
|---|---|---|---|---|---|---|---|---|
| Verify a hash chain | `verify_chain` | `verify_chain_detailed` | `verifyChain` | `VerifyChain` | `verifyChain` | `verifyChain` | `VerifyChain` | `verify_chain` |
| Sign the chain head | `sign_head` | `sign_head` | `signHead` | `SignHead` | `signHead` | `signHead` | `SignHead` | `sign_head` |
| Verify a signed chain | `verify_signed_chain` | `verify_signed_chain` | `verifySignedChain` | `VerifySignedChain` | `verifySignedChain` | `verifySignedChain` | `VerifySignedChain` | `verify_signed_chain` |
| Audit preset | `audit_logger` | `AuditLogger` | `AuditLogger` | `NewAuditLogger` | `AuditLogger` | `AuditLogger` | `AuditLogger` | `AuditLogger` |

## Transports (class names are `PascalCase` everywhere)

`ConsoleTransport`, `FileTransport`, `HTTPTransport`, `SyslogTransport`, `DatadogTransport`,
`NewRelicTransport`, `ElasticsearchTransport`, `AppInsightsTransport`, `OTLPTransport`,
`OTelLogsTransport`, `SQLiteTransport`, `PostgresTransport`, `MySQLTransport`,
`MongoDBTransport`, `RedisTransport`, `DynamoDBTransport`, `KafkaTransport`,
`RabbitMQTransport`, `SQSTransport`, `PubSubTransport`, `CloudWatchTransport`,
`CloudLoggingTransport`, `Neo4jTransport`, `FalkorDBTransport`, `NeptuneTransport`,
`BeaconTransport` (browser only), `CollectingTransport` (tests).

## Runtime and integration helpers

| Concept | Rust | Python | Node / TS | Go | Kotlin | Swift | C# | Ruby |
|---|---|---|---|---|---|---|---|---|
| Serverless flush wrapper | n/a | `with_lambda` | `withLambda` | `WithLambda` | `withLambda` | `withLambda` | `WithLambda` | `with_lambda` |
| Flush on process signals | n/a | `install_signal_handlers` | `installShutdownHandlers` | `InstallShutdownHandlers` | `installShutdownHandlers` | `installShutdownHandlers` | `InstallShutdownHandlers` | `install_signal_handlers` |
| Runtime level: signal | n/a | `install_signal_level_handler` | `installSignalLevelHandler` | `InstallSignalLevelHandler` | n/a | n/a | n/a | `install_signal_level_handler` |
| Runtime level: file / env watcher | `LevelFileWatcher` / `LevelEnvWatcher` | same | same | same | same | same | same | same |
| Patch an LLM SDK | n/a | `instrument.anthropic` `.openai` `.litellm` | `instrument.anthropic` `.openai` | per SDK | per SDK | per SDK | per SDK | per SDK |
| Logging-framework bridge | n/a | stdlib `logging`, `structlog`, `loguru` | pino, winston | `log/slog` | SLF4J | swift-log | `Microsoft.Extensions.Logging` | `Logger` |
| MCP trace propagation | `mcp::propagate` / `mcp::inbound` | `mcp.propagate` / `mcp.inbound` | `propagate` / `inbound` | `Propagate` / `Inbound` | same as Node | same as Node | `Propagate` / `Inbound` | `propagate` / `inbound` |

## Adding a name

1. Add the row here first, in every language.
2. Implement it, with a test that is traceable to a behaviour in `docs/spec/`.
3. If a language cannot support it, write `n/a` here and say why in that binding's README.
