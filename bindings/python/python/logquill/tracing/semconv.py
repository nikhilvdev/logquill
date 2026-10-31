"""LogQuill records -> OpenTelemetry GenAI semantic-convention names.

This is the only module in logquill that contains GenAI attribute names, span
names or operation names. Everything else asks it: a transport passes a
record in and gets a span name, a span kind and a dict of attributes back.

The GenAI conventions are still marked *Development* upstream, and the names
have already changed more than once (`gen_ai.system` became
`gen_ai.provider.name`; `prompt_tokens`/`completion_tokens` became
`input_tokens`/`output_tokens`). So the names live in one table per convention
generation, pinned to a named release, and moving to a newer release is an edit
to this file and nothing else. It never imports OpenTelemetry, so it can be
tested — and read — on its own.

Pinned to the GenAI conventions as published with semantic-conventions
`CONVENTION_VERSION`.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from logquill.core.records import LogRecord

_logger = logging.getLogger("logquill")

#: The semantic-conventions release the `latest` names below come from.
CONVENTION_VERSION = "1.44.0"

#: The value of `OTEL_SEMCONV_STABILITY_OPT_IN` that asks for the latest
#: experimental GenAI conventions.
OPT_IN_ENV = "OTEL_SEMCONV_STABILITY_OPT_IN"
OPT_IN_LATEST = "gen_ai_latest_experimental"

#: `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` turns on prompt and
#: completion capture, the same switch other GenAI instrumentation uses.
CAPTURE_CONTENT_ENV = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"

#: Attributes LogQuill adds that no convention defines. Namespaced so they can't
#: collide with anything upstream adds later.
_EXT = "logquill."

SpanKindName = Literal["client", "internal"]


@dataclass(frozen=True)
class Convention:
    """One generation of GenAI attribute names. `names` maps LogQuill's own
    keys (the ones used by the functions below) to the attribute a backend
    receives."""

    version: str
    names: Mapping[str, str] = field(repr=False)


_LATEST = Convention(
    version=CONVENTION_VERSION,
    names={
        "operation": "gen_ai.operation.name",
        "provider": "gen_ai.provider.name",
        "request_model": "gen_ai.request.model",
        "response_model": "gen_ai.response.model",
        "input_tokens": "gen_ai.usage.input_tokens",
        "output_tokens": "gen_ai.usage.output_tokens",
        "finish_reasons": "gen_ai.response.finish_reasons",
        "agent_name": "gen_ai.agent.name",
        "agent_id": "gen_ai.agent.id",
        "conversation_id": "gen_ai.conversation.id",
        "tool_name": "gen_ai.tool.name",
        "tool_call_id": "gen_ai.tool.call.id",
        "input_messages": "gen_ai.input.messages",
        "output_messages": "gen_ai.output.messages",
    },
)

#: The names in use before the provider and token-count renames — for a backend
#: or dashboard that hasn't caught up. Only ever chosen explicitly.
_LEGACY = Convention(
    version="legacy",
    names={
        **_LATEST.names,
        "provider": "gen_ai.system",
        "input_tokens": "gen_ai.usage.prompt_tokens",
        "output_tokens": "gen_ai.usage.completion_tokens",
    },
)

_CONVENTIONS: dict[str, Convention] = {"latest": _LATEST, "legacy": _LEGACY}

#: What is used when neither the caller nor the environment says. Kept separate
#: from `_LATEST` on purpose: when a newer generation is added, the default can
#: stay on the one people already export while `gen_ai_latest_experimental`
#: opts in to the new one, which is how OpenTelemetry asks for this to work.
_DEFAULT = _LATEST

_ERROR_TYPE = "error.type"

# operation names
INVOKE_AGENT = "invoke_agent"
EXECUTE_TOOL = "execute_tool"
CHAT = "chat"
TEXT_COMPLETION = "text_completion"

#: `provider` when nothing says which one — the attribute is required upstream.
UNKNOWN_PROVIDER = "unknown"

_warned_opt_in: set[str] = set()


def resolve_convention(
    explicit: str | None = None, environ: Mapping[str, str] | None = None
) -> Convention:
    """Choose the naming generation. An explicit `explicit` ("latest" or
    "legacy") wins; otherwise `OTEL_SEMCONV_STABILITY_OPT_IN` is read (a
    comma-separated list, of which `gen_ai_latest_experimental` selects the
    latest names); otherwise the default is used.

    Old and new names are never emitted together: an opt-in value asking for
    that (anything ending `/dup`) is ignored with a one-time warning, and
    exactly one generation is used.
    """
    if explicit is not None:
        try:
            return _CONVENTIONS[explicit]
        except KeyError:
            raise ValueError(
                f"semconv must be one of {', '.join(sorted(_CONVENTIONS))}, got {explicit!r}"
            ) from None

    env = os.environ if environ is None else environ
    tokens = [token.strip() for token in env.get(OPT_IN_ENV, "").split(",") if token.strip()]
    for token in tokens:
        if token.endswith("/dup") and token not in _warned_opt_in:
            _warned_opt_in.add(token)
            _logger.warning(
                "%s=%s: LogQuill never emits old and new GenAI attribute names together — "
                "using one generation only",
                OPT_IN_ENV,
                token,
            )
    return _CONVENTIONS["latest"] if OPT_IN_LATEST in tokens else _DEFAULT


def capture_content_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Whether the environment switches on prompt/completion capture."""
    env = os.environ if environ is None else environ
    return env.get(CAPTURE_CONTENT_ENV, "").strip().lower() == "true"


@dataclass(frozen=True)
class SpanSpec:
    """A span a record describes: its name, kind, attributes, and — because a
    record is written when the work *ends* — how long it lasted."""

    name: str
    kind: SpanKindName
    attributes: dict[str, Any]
    duration_ms: float | None = None
    is_error: bool = False
    events: tuple[tuple[str, dict[str, Any]], ...] = ()


def _meta(record: LogRecord) -> Mapping[str, Any]:
    meta = record.get("meta")
    return meta if isinstance(meta, dict) else {}


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _json(value: Any) -> str:
    try:
        return json.dumps(value, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


def _error_type(meta: Mapping[str, Any]) -> str | None:
    error = _string(meta.get("error"))
    if error is None:
        return None
    head = error.split(":", 1)[0].strip()
    return head if head and " " not in head else "_OTHER"


def is_span_record(record: LogRecord) -> bool:
    """A record `Logger.span()` wrote on exit."""
    meta = _meta(record)
    return meta.get("kind") == "span" and isinstance(meta.get("span_id"), str)


def is_llm_record(record: LogRecord) -> bool:
    """A record carrying an `llm` block."""
    return isinstance(record.get("llm"), dict)


def is_tool_record(record: LogRecord) -> bool:
    """An `.action()` naming the tool it called, in `meta.tool`."""
    meta = _meta(record)
    return meta.get("kind") == "action" and _string(meta.get("tool")) is not None


def _common(convention: Convention, meta: Mapping[str, Any], attributes: dict[str, Any]) -> None:
    names = convention.names
    thread_id = _string(meta.get("thread_id"))
    if thread_id:
        attributes[names["conversation_id"]] = thread_id
    agent_name = _string(meta.get("agent_name"))
    if agent_name:
        attributes[names["agent_name"]] = agent_name
    if run_id := _string(meta.get("run_id")):
        attributes[_EXT + "run_id"] = run_id
    if node := _string(meta.get("node_name")):
        attributes[_EXT + "node_name"] = node
    retry = meta.get("retry_count")
    if isinstance(retry, int) and not isinstance(retry, bool):
        attributes[_EXT + "retry_count"] = retry
    error_type = _error_type(meta)
    if error_type:
        attributes[_ERROR_TYPE] = error_type


def _events(meta: Mapping[str, Any]) -> tuple[tuple[str, dict[str, Any]], ...]:
    diff = meta.get("state_diff")
    if isinstance(diff, dict):
        return ((_EXT + "state_diff", {_EXT + "state": _json(diff)}),)
    return ()


def llm_spec(
    record: LogRecord,
    convention: Convention,
    *,
    default_provider: str = UNKNOWN_PROVIDER,
    capture_content: bool = False,
) -> SpanSpec:
    """The inference span for a record with an `llm` block: `chat {model}`, or
    `text_completion {model}` when `meta.operation` says so. Prompt and
    completion text (`meta.input_messages`/`output_messages`) are added only if
    `capture_content` is true."""
    names = convention.names
    llm: Mapping[str, Any] = record.get("llm") or {}
    meta = _meta(record)

    operation = TEXT_COMPLETION if meta.get("operation") == TEXT_COMPLETION else CHAT
    model = _string(llm.get("model"))
    attributes: dict[str, Any] = {
        names["operation"]: operation,
        names["provider"]: _string(meta.get("provider")) or default_provider,
    }
    if model:
        attributes[names["request_model"]] = model
    if response_model := _string(meta.get("response_model")):
        attributes[names["response_model"]] = response_model
    if isinstance(llm.get("tokens_in"), int):
        attributes[names["input_tokens"]] = llm["tokens_in"]
    if isinstance(llm.get("tokens_out"), int):
        attributes[names["output_tokens"]] = llm["tokens_out"]
    if finish_reason := _string(llm.get("finish_reason")):
        attributes[names["finish_reasons"]] = [finish_reason]
    if isinstance(llm.get("cost_usd"), (int, float)):
        attributes[_EXT + "cost_usd"] = float(llm["cost_usd"])
    if capture_content:
        for key in ("input_messages", "output_messages"):
            if meta.get(key) is not None:
                attributes[names[key]] = _json(meta[key])
    _common(convention, meta, attributes)

    return SpanSpec(
        name=f"{operation} {model}" if model else operation,
        kind="client",
        attributes=attributes,
        duration_ms=_number(llm.get("latency_ms")),
        is_error=_error_type(meta) is not None or record.get("level") in ("ERROR", "FATAL"),
        events=_events(meta),
    )


def tool_spec(record: LogRecord, convention: Convention) -> SpanSpec:
    """The `execute_tool {tool}` span for an `.action()` that names its tool."""
    names = convention.names
    meta = _meta(record)
    tool = str(meta["tool"])
    attributes: dict[str, Any] = {names["operation"]: EXECUTE_TOOL, names["tool_name"]: tool}
    if call_id := _string(meta.get("tool_call_id")):
        attributes[names["tool_call_id"]] = call_id
    _common(convention, meta, attributes)
    return SpanSpec(
        name=f"{EXECUTE_TOOL} {tool}",
        kind="internal",
        attributes=attributes,
        duration_ms=_number(meta.get("duration_ms")),
        is_error=_error_type(meta) is not None,
        events=_events(meta),
    )


def span_spec(
    record: LogRecord,
    convention: Convention,
    *,
    default_provider: str = UNKNOWN_PROVIDER,
) -> SpanSpec:
    """The span for a `Logger.span()` record. A span that says it is an agent
    run — `meta.operation="invoke_agent"`, or it names an `agent_name` — is an
    `invoke_agent {agent}` span; any other span keeps its own name and carries
    no GenAI operation. (A span isn't assumed to be an agent run just because
    it is the outermost one: that would rename ordinary spans.)"""
    names = convention.names
    meta = _meta(record)
    attributes: dict[str, Any] = {}
    _common(convention, meta, attributes)

    is_agent_run = meta.get("operation") == INVOKE_AGENT or _string(meta.get("agent_name"))
    if is_agent_run:
        agent = _string(meta.get("agent_name")) or str(record["logger"])
        attributes[names["operation"]] = INVOKE_AGENT
        attributes[names["provider"]] = _string(meta.get("provider")) or default_provider
        attributes[names["agent_name"]] = agent
        if agent_id := _string(meta.get("agent_id")):
            attributes[names["agent_id"]] = agent_id
        name = f"{INVOKE_AGENT} {agent}"
        kind: SpanKindName = "client"
    else:
        name = str(record["message"])
        kind = "internal"

    return SpanSpec(
        name=name,
        kind=kind,
        attributes=attributes,
        duration_ms=_number(meta.get("duration_ms")),
        is_error=record.get("level") in ("ERROR", "FATAL"),
        events=_events(meta),
    )


def log_attributes(record: LogRecord, convention: Convention) -> dict[str, Any]:
    """The GenAI attributes to put on a *log* record: the `llm` block's usage
    and model, so a log line about an LLM call is queryable by the same names
    as its span."""
    if not is_llm_record(record):
        return {}
    llm: Mapping[str, Any] = record["llm"]
    names = convention.names
    attributes: dict[str, Any] = {names["operation"]: CHAT}
    if model := _string(llm.get("model")):
        attributes[names["request_model"]] = model
    if isinstance(llm.get("tokens_in"), int):
        attributes[names["input_tokens"]] = llm["tokens_in"]
    if isinstance(llm.get("tokens_out"), int):
        attributes[names["output_tokens"]] = llm["tokens_out"]
    if finish_reason := _string(llm.get("finish_reason")):
        attributes[names["finish_reasons"]] = [finish_reason]
    if isinstance(llm.get("cost_usd"), (int, float)):
        attributes[_EXT + "cost_usd"] = float(llm["cost_usd"])
    return attributes


def epoch_ns(timestamp: str) -> int:
    """A record's ISO 8601 timestamp as nanoseconds since the Unix epoch."""
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return int(parsed.timestamp() * 1_000_000_000)
