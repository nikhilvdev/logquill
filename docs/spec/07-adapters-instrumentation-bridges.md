# 07 — Framework adapters, SDK patching and logging bridges

All of this lives in each language's SDK layer. An adapter holds a logger (`self.log`;
the intended use is `log.child("agent").use(RunPlugin())`) and does **field renaming, not
translation**: a framework's run/parent ids are written straight onto `meta.span_id` /
`meta.parent_span_id`. Any code in a handler that raises must not break the framework.

## LangChain (P and J)

Python (`LangChainAdapter`, a `BaseCallbackHandler`), ids: `span_id = str(run_id)`,
`parent_span_id = str(parent_run_id)` when given.

| Callback | Call |
|---|---|
| `on_chain_start` | open `log.span(name, span_id, parent_span_id)` and remember it by run id; `name` = `serialized["name"]` if a non-empty string, else `"chain"` |
| `on_chain_end` | close that span |
| `on_chain_error` | close it with the error (record at ERROR with `meta.error`) |
| `on_llm_start` | remember start time; `action("llm_start", ids)` |
| `on_llm_end` | `observation("llm_end", ids, duration_ms)` (3 decimals) |
| `on_llm_error` | drop the timer; `error("llm_error", error=str(e), ids)` |
| `on_agent_action` | `action(action.tool or "agent_action")` with **no** span ids (the enclosing chain's run id is not a fresh id; the ambient span supplies the parent) |
| `on_agent_finish` | `decision("agent_finish")` |
| `on_tool_start` | remember start; `action(name or "tool", ids)` |
| `on_tool_end` | `observation("tool_end", ids, duration_ms)` |
| `on_tool_error` | drop timer; `error("tool_error", error=str(e), ids)` |

JavaScript (`LangChainAdapter extends BaseCallbackHandler`, `name = "logquill"`): the same
mapping with these differences: there is no open span scope, so chain start stores the
time and name and chain end writes **one** record `info(name, {kind:"span", span_id,
parent_span_id, duration_ms})` (chain error: `error(name, {…, error})`); the name falls
back to the last segment of `serialized.id` before the generic word; `handleAgentAction`
writes `action(tool || "agent_action", {parent_span_id: runId})` and `handleAgentEnd`
`decision("agent_finish", {parent_span_id: runId})`; errors are formatted `"<Name>:
<message>"` for an `Error`, else `String(error)`. Records inside a chain are therefore
not auto-parented in JS (decision D28).

## LangGraph

P: subclass of the LangChain adapter and of LangGraph's `GraphCallbackHandler` (needed
because LangGraph only delivers interrupt/resume to that class). `on_interrupt(event)` →
`observation("graph_interrupted", checkpoint_id, status, checkpoint_ns (list, only if
non-empty), parent_span_id = str(event.run_id) (if not null), interrupts = [{id, value}…])`.
`on_resume(event)` → `action("graph_resumed", the same checkpoint fields)`. J:
`LangGraphAdapter` is the LangChain adapter under another name; LangGraph.js has no
equivalent hooks.

## CrewAI (P)

A listener on CrewAI's event bus; registers on construction (hold a reference). Ids come
from the events: `span_id = event.event_id`, `parent_span_id = event.parent_event_id`;
"ended" events use `event.started_event_id or event.event_id` as the span id.

| Event | Call |
|---|---|
| crew kickoff started / completed / failed | open `span("crew:<crew_name or 'crew'>")` / close / close with `RuntimeError(error)` |
| task started / completed / failed | open `span("task:<task_name or task_id or 'task'>")` / close / close with `error_type(error)` or `RuntimeError` |
| agent execution started / completed / error | `action("agent:<role>", ids)` + remember `event.timestamp` / `observation(…, duration_ms)` / `error(…, error=…)` |
| tool usage started / finished / error | `action(tool_name, ids)` / `observation(tool_name, duration_ms = finished_at − started_at)` / `error(tool_name, error=str(e))` |
| LLM call started / completed / failed | `action("llm_call")` + timestamp / `observation("llm_call", duration_ms)` / `error("llm_call", error=…)` |

If a listener is attached mid-run, an "ended" span event with no remembered "started" is
dropped rather than guessed; an agent/LLM "ended" event with no start time is logged
without `duration_ms`.

## AutoGen (P)

A `logging.Handler` attached to `autogen_core.EVENT_LOGGER_NAME` (level forced to INFO);
active at construction; `close()` detaches. `emit` reads `record.msg.kwargs` (ignores a
record without a dict `kwargs`); `type` = `kwargs["type"]`; `meta` = the other keys.

| `type` | Call |
|---|---|
| `MessageHandlerException` | `error("message_handler_exception", error=<exception>)` |
| `AgentConstructionException` | `error("agent_construction_exception", error=<exception>)` |
| `LLMStreamStart` / `LLMStreamEnd` | `action("llm_stream")` / `observation("llm_stream")` |
| `LLMCall` / `ToolCall` | `observation("llm_call")` / `observation("tool_call")` |
| `Message` / `MessageDropped` | `action("message")` / `observation("message_dropped")` |
| anything else | `action(type or "autogen_event")` |

No span ids exist in AutoGen's events, so there is no tree. It covers Microsoft
`autogen-core` only, not AG2.

## LlamaIndex (P)

Two handlers on the global dispatcher. Span handler: on a span exit/drop, name = span id
up to the first `-`; `meta = {span_id: id, kind: "span", duration_ms: round(duration × 1000, 3),
parent_span_id}`; `info(name)` or, on a drop, `error(name, error="<Type>: <msg>")`. Event
handler: skip class names ending `InProgressEvent` or `DeltaReceivedEvent`; `meta.parent_span_id
= event.span_id` if present; class name ending `ErrorEvent` → `error(name, error=str(event.exception
or event.error or ""))`; ending `StartEvent` → `action(name)`; ending `EndEvent` →
`observation(name)`; anything else → `action(name)`.

## OpenAI Agents SDK (P)

`RunHooks` subclass, passed as `Runner.run(..., hooks=…)`. State keyed by the run context
object's identity (the SDK gives no run id).

| Hook | Call |
|---|---|
| `on_agent_start` | push `span(agent.name or "agent", span_id=<new>, parent_span_id=<top of this context's stack>, operation="invoke_agent", agent_name=…)` |
| `on_agent_end` | pop and close the top span; forget the stack when empty |
| `on_llm_start` | remember a monotonic start |
| `on_llm_end` | `llm_call(model=agent.model if a string, latency_ms, tokens_in=usage.input_tokens, tokens_out=usage.output_tokens)` |
| `on_tool_start` | remember start under (context, tool name); `action("call <tool>", tool=<name>, agent_name=…)` |
| `on_tool_end` | `observation("<tool> done", tool=<name>, duration_ms)` |
| `on_handoff` | `decision("handoff", from_agent, to_agent)` |

Tagging actions with `tool=` is what makes them `execute_tool` spans in the OTLP export
and gets `retry_count` tracked. Known limit: two concurrent calls of the same tool in one
run can swap durations.

## SDK patching (`instrument`)

Contract (both languages): calling an instrumenter patches the provider SDK; calling it
again without `uninstrument()` raises (P `logquill.instrument.<name> is already active —
call .uninstrument() first if you want to change its logger or options`; J
`logquill/instrument <name> is already active. Call <name>.uninstrument() first to change
its logger.`); `uninstrument()` restores every patched method and is safe when idle;
`active` reports the state. Importing the library never imports a provider SDK. Wrappers
pass **streaming** calls through untouched (a tracked gap), time the call with a monotonic
clock, and record through `llm_call`; any exception while recording is logged
(`logquill.instrument: failed to record a call`) and never reaches the caller.

| Provider | Patched call | Fields recorded |
|---|---|---|
| Anthropic | `messages.create` (sync and async) | model = response model or request `model`; `tokens_in/out` = `usage.input_tokens/output_tokens`; `finish_reason` = `stop_reason`; `provider="anthropic"` |
| OpenAI chat | `chat.completions.create` | `usage.prompt_tokens/completion_tokens`; `finish_reason` = first choice's; `provider="openai"` |
| OpenAI responses | `responses.create` | `usage.input_tokens/output_tokens`; `finish_reason` = `incomplete_details.reason` if a string, else `status`; `provider="openai"` |
| LiteLLM (P) | `litellm.completion` / `acompletion` | prompt/completion tokens; first choice's finish reason; `provider` = `response._hidden_params["custom_llm_provider"]` if a string |

P patches the SDK's classes. J is handed a **client instance** and patches the prototype of
the relevant resource (so every client of that copy of the SDK is covered); it keeps the
SDK's own promise subclass working by hooking `then` on the returned object and recording
once on first consumption; consumers of `.asResponse()` / `.withResponse()` are not
recorded; a missing method throws `logquill/instrument <name>: <method>() was not found on
the client you passed. Pass an instance of the official SDK's client.`

## MCP

See `03-privacy-tracing-otel.md` (`propagate`, `inbound`). Both languages expose the same
two keys and the `meta.mcp.{server,tool,run_id}` context.

## Logging bridges

**stdlib `logging` (P `LogQuillHandler(logger, level=NOTSET)`).** `emit` maps the level:
≥50 FATAL, ≥40 ERROR, ≥30 WARN, ≥20 INFO, ≥10 DEBUG, else TRACE; message = `getMessage()`;
`meta` = every attribute of the stdlib record **except** `name, msg, args, levelname,
levelno, pathname, filename, module, exc_info, exc_text, stack_info, lineno, funcName,
created, msecs, relativeCreated, thread, threadName, processName, process, taskName` and
`message` (so `extra=` fields arrive); `exc_info`, when present, is passed on so a traceback
is formatted into `meta.stack`; a translation failure goes to `handleError`, never raised.
**Defect (D24):** ignore records whose name is `logquill` or starts `logquill.`.

**pino (J `LogQuillPinoDestination(logger, {levelMap})`).** A `writable` object with
`write(chunk)`: split on `\n`, skip blank lines, parse each as JSON (skip invalid or
non-objects); `level` (number, default 30) mapped 10 TRACE, 20 DEBUG, 30 INFO, 40 WARN,
50 ERROR, 60 FATAL (unmapped → INFO); message = `msg` if a string else `""`; `meta` = every
other key. Returns true.

**winston (J `LogQuillWinstonTransport(logger, {levelMap, …transport options})`).** Separate
entry point (it must extend winston's class). `log(info, callback)`: emits `logged` on the
next tick; level names `error`→ERROR, `warn`→WARN, `info`→INFO, `http`→INFO, `verbose`→DEBUG,
`debug`→DEBUG, `silly`→TRACE (unmapped → INFO); message = string or its string form;
`meta` = every other key; winston's own level filter runs first and the LogQuill logger's
level filters again.

## Browser build (J)

Includes the core logger, levels, JSON/text/logfmt formatters, the context, redact, PII and
sampling plugins, the console transport and the beacon transport; excludes every Node-only
module (a test fails the build if a Node built-in is imported). Differences: span nesting
and bound context use a plain stack instead of async-local storage, popped when the
callback's promise settles — correct for one open block at a time, **not** isolated across
concurrent awaits. Span ids from `crypto.getRandomValues` (8 bytes, hex). `BeaconTransport`:
batch default 20 lines (beacon payloads are capped); flush sends the lines joined by `\n`
via `navigator.sendBeacon`, falling back to `fetch(url, {method:"POST", body, keepalive:true})`
(a failure is reported with `console.error` and never thrown); injectable sender.

## Package surface (what users import today)

P: one package, extras per integration (see 08). J entry points: `.` (core), `./langchain`
(`LangChainAdapter`, `LangGraphAdapter`), `./winston`, `./instrument` (anthropic, openai,
mcp), `./browser`. Optional peers are loaded lazily by non-literal specifier so that the main
entry never requires them.
