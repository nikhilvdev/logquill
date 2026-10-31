#!/usr/bin/env python3
"""Builds schema/corpus/<group>.json from the hand-written inputs and the two references.

    python3 scripts/corpus/build.py            # write every group
    python3 scripts/corpus/build.py --check    # fail if a committed group is out of date
    python3 scripts/corpus/build.py levels     # only the named groups

Each case's `expected` is what the settled decisions in docs/spec/00-decisions.md say. Where
a reference disagrees with it, the reference's output is kept under `differs` and `rule` names
the decision, so every divergence is auditable. A disagreement no rule explains stops the
build: either a decision is missing or a reference changed.

Environment:
    LOGQUILL_PY_DIR   an unpacked `git archive v2.0.0` tree (default: unpacked on demand)
    LOGQUILL_JS_DIR   a logquill-js checkout with `npm ci` done (default: ../logquill-js)
"""

import collections
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CORPUS = ROOT / "schema" / "corpus"
GROUPS = [
    "canonical-json", "hash-chain", "content-policy", "field-classes", "levels", "records",
    "trace-headers", "otel-ids", "redaction", "formatters", "ecs", "semconv",
    "sql-rows", "graph-rows", "trace-tree",
    "sampling", "rate-limit", "adaptive-sampling", "flight-recorder", "run-summary", "run-plugin",
    "alert-dedupe",
]

PLACEHOLDER = "<content capture is off — set the logger's content policy to capture it>"
LLM_INT = ("tokens_in", "tokens_out")
LLM_NUMBER = ("cost_usd", "latency_ms")
LLM_STRING = ("model", "finish_reason")
LLM_ALL = "model, tokens_in, tokens_out, cost_usd, latency_ms, finish_reason"
LEVELS = {"TRACE": 5, "DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40, "FATAL": 50}
LEGACY_META = {
    "runId": "run_id", "spanId": "span_id", "parentSpanId": "parent_span_id",
    "traceId": "trace_id", "durationMs": "duration_ms", "prevHash": "prev_hash",
}


SCENARIO_GROUPS = {
    "sampling", "rate-limit", "adaptive-sampling", "flight-recorder", "run-summary", "run-plugin", "alert-dedupe",
}


class Unsettled(Exception):
    """A reference disagrees with the expected output and no decision explains it."""


def same(a, b):
    """Equality that tells 1 from 1.0 and 0 from false, the way a JSON reader does."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def scrub(value):
    """Replaces lone surrogates, which a reference may print but strict JSON readers refuse."""
    if isinstance(value, str):
        return "".join("\ufffd" if 0xD800 <= ord(c) <= 0xDFFF else c for c in value)
    if isinstance(value, dict):
        return {scrub(k): scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def js(value):
    """Compact JSON text of a value, the way the core renders values in messages."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "array" if isinstance(value, list) else "object"


# --------------------------------------------------------------------- references

def python_tree():
    given = os.environ.get("LOGQUILL_PY_DIR")
    if given:
        return given
    target = tempfile.mkdtemp(prefix="logquill-ref-py-")
    archive = subprocess.run(["git", "archive", "v2.0.0"], cwd=ROOT, check=True, capture_output=True)
    subprocess.run(["tar", "-x", "-C", target], input=archive.stdout, check=True)
    return target


def run_reference(kind, group, tree):
    inputs = HERE / "inputs" / f"{group}.json"
    if kind == "python":
        command, env = [sys.executable, "-I", str(HERE / "ref_py.py"), str(inputs)], {"LOGQUILL_PY_DIR": tree}
    else:
        command, env = ["node", str(HERE / "ref_js.mjs"), str(inputs)], {"LOGQUILL_JS_DIR": tree}
    done = subprocess.run(command, capture_output=True, text=True, env={**os.environ, **env})
    if done.returncode != 0:
        raise SystemExit(f"{kind} reference failed on {group}:\n{done.stderr}")
    return json.loads(done.stdout)


def js_commit(tree):
    done = subprocess.run(["git", "-C", tree, "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    return done.stdout.strip() or "unknown"


# --------------------------------------------------------------------- settled behaviour

def settle_level(value):
    if isinstance(value, str):
        name = "".join(c.upper() if c.isascii() else c for c in value)
        return {"level": name} if name in LEVELS else {"error": f"Unknown log level: {value}"}
    if isinstance(value, bool) or value is None or isinstance(value, (list, dict)):
        return {"error": f"level must be a name or a number, got {json_type(value)}"}
    if isinstance(value, int) and value in LEVELS.values():
        return {"level": next(n for n, w in LEVELS.items() if w == value)}
    return {"error": f"Unknown log level: {js(value)}"}


def llm_valid(name, value):
    if name in LLM_INT:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    if name in LLM_NUMBER:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
    return name in LLM_STRING and isinstance(value, str)


def llm_expected(name):
    if name in LLM_INT:
        return "a non-negative integer"
    if name in LLM_NUMBER:
        return "a non-negative number"
    if name in LLM_STRING:
        return "a string"
    return f"one of {LLM_ALL}"


def settle_llm(fields):
    block, warnings = {}, []
    for name, value in fields.items():
        if value is None:
            continue
        if llm_valid(name, value):
            block[name] = value
        else:
            warnings.append(f"llm_call: ignoring {name}={js(value)} — expected {llm_expected(name)}")
    return {"block": block or None, "warnings": warnings}


def settle_parse(raw):
    if not isinstance(raw, dict):
        return {"error": f"a log record must be a JSON object, got {json_type(raw)}"}
    legacy = "schema_version" not in raw
    version = "1.0" if legacy else raw["schema_version"]
    match = re.fullmatch(r"([0-9]+)\.([0-9]+)", version) if isinstance(version, str) else None
    if match is None:
        return {"error": f"schema_version must be a string like '2.0', got {js(version)} — is this a LogQuill record?"}
    if int(match.group(1)) > 2:
        return {"error": f"this record has schema_version {js(version)}, newer than the '2.0' this logquill understands — upgrade logquill to read it"}
    for field in ("timestamp", "level", "logger", "message"):
        if not isinstance(raw.get(field), str):
            return {"error": f"record field '{field}' must be a string, got {js(raw.get(field))}"}
    if raw["level"] not in LEVELS:
        return {"error": f"record level {js(raw['level'])} isn't one of TRACE, DEBUG, INFO, WARN, ERROR, FATAL (upper case)"}
    meta = raw.get("meta", {})
    if not isinstance(meta, dict):
        return {"error": f"record field 'meta' must be an object, got {json_type(meta)}"}
    if legacy:
        meta = {LEGACY_META.get(k, k): v for k, v in meta.items()}
    llm = raw.get("llm")
    if llm is not None and not isinstance(llm, dict):
        return {"error": f"record field 'llm' must be an object, got {json_type(llm)}"}
    record = {"schema_version": version}
    for field in ("timestamp", "level", "logger", "message"):
        record[field] = raw[field]
    record["meta"] = meta
    if llm is not None:
        record["llm"] = llm
    known = {"schema_version", "timestamp", "level", "logger", "message", "meta", "llm"}
    record.update({k: v for k, v in raw.items() if k not in known})
    return {"record": record}


FIELD_CLASSES = {
    "password": "secret", "token": "secret", "secret": "secret", "api_key": "secret",
    "authorization": "secret", "email": "pii", "ssn": "pii", "phone": "pii",
    "input_messages": "content", "output_messages": "content", "system_instructions": "content",
    "tool_arguments": "content", "tool_result": "content", "state_diff": "content",
}
MAX_DEPTH = 50
PII_PATTERNS = [
    ("email", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ("ssn", r"\b\d{3}-\d{2}-\d{4}\b"),
    ("credit_card", r"\b(?:\d[ -]?){13,16}\b"),
    ("phone", r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    ("openai_api_key", r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    ("anthropic_api_key", r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    ("aws_access_key_id", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("github_token", r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    ("slack_token", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ("private_key_block", r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----"),
    ("bearer_token", r"\bBearer\s+[A-Za-z0-9\-_.]{20,}\b"),
]


def redact_text(text, replacement):
    spans = []
    for _name, pattern in PII_PATTERNS:
        flags = re.ASCII | (re.DOTALL if pattern.startswith("-----BEGIN") else 0)
        spans += [(m.start(), m.end()) for m in re.finditer(pattern, text, flags)]
    spans.sort(key=lambda span: (span[0], -span[1]))
    out, cursor, last_end = [], 0, -1
    for start, end in spans:
        if start >= last_end:
            out += [text[cursor:start], replacement]
            cursor, last_end = end, end
    out.append(text[cursor:])
    return "".join(out)


def settle_redaction(data):
    op = data["op"]
    replacement = data.get("replacement", "***")
    if op in ("redact", "redact_local"):
        keys = {k.lower() for k in data.get("keys", ["password", "token", "secret", "api_key", "authorization"])}
        for cls in data.get("classes") or []:
            keys |= {k.lower() for k, c in FIELD_CLASSES.items() if c == cls}
        if op == "redact_local":
            return {"text": replacement if data["name"].lower() in keys else data["text"]}

        def walk(value, depth):
            if depth > MAX_DEPTH and isinstance(value, (str, list, dict)):
                return replacement
            if isinstance(value, dict):
                return {k: replacement if k.lower() in keys else walk(v, depth + 1) for k, v in value.items()}
            if isinstance(value, list):
                return [walk(v, depth + 1) for v in value]
            return value
    else:
        if op == "pii_local":
            return {"text": redact_text(data["text"], replacement)}

        def walk(value, depth):
            if depth > MAX_DEPTH and isinstance(value, (str, list, dict)):
                return replacement
            if isinstance(value, str):
                return redact_text(value, replacement)
            if isinstance(value, dict):
                return {k: walk(v, depth + 1) for k, v in value.items()}
            if isinstance(value, list):
                return [walk(v, depth + 1) for v in value]
            return value
    return {"meta": walk(data["meta"], 0)}


CONTROL = "".join(chr(c) for c in range(0x20)) + "\x7f\x85\u2028\u2029"
QUOTE_TRIGGERS = set(CONTROL) | set(' "=\\')
ESCAPES = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def finite(value):
    """Replaces NaN and the infinities with null, as JSON lines write them (D31)."""
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite(v) for v in value]
    return value


def jline(value):
    """Compact JSON, UTF-8 (D32), floats as Python prints them (D4), non-finite as null (D31)."""
    return json.dumps(finite(value), ensure_ascii=False, separators=(",", ":"))


def lf_quote(text):
    if text and not any(c in QUOTE_TRIGGERS for c in text):
        return text
    body = "".join(ESCAPES.get(c) or (f"\\u{ord(c):04x}" if c in CONTROL else c) for c in text)
    return f'"{body}"'


def lf_key(raw):
    return "".join("_" if c in QUOTE_TRIGGERS else c for c in str(raw)) or "_"


def lf_scalar(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return "null" if finite(value) is None else str(value)
    if isinstance(value, str):
        return lf_quote(value)
    return lf_quote(jline(value))


def lf_flatten(prefix, value, depth, out):
    if isinstance(value, dict) and value and depth < 5:
        for key, child in value.items():
            lf_flatten(f"{prefix}.{lf_key(key)}", child, depth + 1, out)
    else:
        out.append(f"{prefix}={lf_scalar(value)}")


def shown(value, default):
    if value is None:
        return default
    return value if isinstance(value, str) else jline(value)


def settle_format(op, record):
    meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
    llm = record.get("llm")
    if op == "json":
        return {"line": jline(record)}
    if op == "text":
        meta = dict(meta)
        stack = meta.get("stack")
        if isinstance(stack, str) and stack:
            del meta["stack"]
        else:
            stack = None
        line = f"{shown(record.get('timestamp'), '?')} {shown(record.get('level'), '?'):<5} {shown(record.get('logger'), '?')}: {shown(record.get('message'), '')}"
        if llm:
            line += f" llm={jline(llm)}"
        if meta:
            line += f" {jline(meta)}"
        if stack is not None:
            line += "\n" + stack.rstrip("\n")
        return {"line": line}
    pairs = [
        f"timestamp={lf_quote(shown(record.get('timestamp'), '?'))}",
        f"level={lf_quote(shown(record.get('level'), '?'))}",
        f"logger={lf_quote(shown(record.get('logger'), '?'))}",
        f"message={lf_quote(shown(record.get('message'), ''))}",
    ]
    if llm:
        lf_flatten("llm", llm, 1, pairs)
    for key, value in meta.items():
        name = lf_key(key)
        if name in ("timestamp", "level", "logger", "message"):
            name = f"meta.{name}"
        lf_flatten(name, value, 1, pairs)
    return {"line": " ".join(pairs)}


def settle_parse_logfmt(line):
    fields = {}
    pattern = r'([^\s="]+)(?:=("(?:[^"\\]|\\.)*"|[^\s"]*))?'
    for found in re.finditer(pattern, line, re.ASCII):
        key, raw = found.group(1), found.group(2)
        if raw is None:
            fields[key] = ""
        elif len(raw) >= 2 and raw.startswith('"') and raw.endswith('"'):
            def unescape(m):
                code = m.group(1)
                if len(code) == 11:
                    high, low = int(code[1:5], 16), int(code[7:11], 16)
                    return chr(0x10000 + ((high - 0xD800) << 10) + (low - 0xDC00))
                if code[0] == "u" and len(code) == 5:
                    point = int(code[1:], 16)
                    return m.group(0) if 0xD800 <= point <= 0xDFFF else chr(point)
                return {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}.get(code, m.group(0))
            fields[key] = re.sub(r"\\(u[dD][89abAB][0-9a-fA-F]{2}\\u[dD][c-fC-F][0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|.)", unescape, raw[1:-1], flags=re.DOTALL)
        else:
            fields[key] = raw
    return {"fields": fields}


LATEST_NAMES = {
    "operation": "gen_ai.operation.name", "provider": "gen_ai.provider.name",
    "request_model": "gen_ai.request.model", "response_model": "gen_ai.response.model",
    "input_tokens": "gen_ai.usage.input_tokens", "output_tokens": "gen_ai.usage.output_tokens",
    "finish_reasons": "gen_ai.response.finish_reasons", "agent_name": "gen_ai.agent.name",
    "agent_id": "gen_ai.agent.id", "conversation_id": "gen_ai.conversation.id",
    "tool_name": "gen_ai.tool.name", "tool_call_id": "gen_ai.tool.call.id",
    "input_messages": "gen_ai.input.messages", "output_messages": "gen_ai.output.messages",
}
LEGACY_NAMES = {**LATEST_NAMES, "provider": "gen_ai.system",
                "input_tokens": "gen_ai.usage.prompt_tokens", "output_tokens": "gen_ai.usage.completion_tokens"}
CAPTURE = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"


def strict_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def strict_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def sem_string(value):
    return value if isinstance(value, str) and value else None


def sem_error_type(meta):
    error = sem_string(meta.get("error"))
    if error is None:
        return None
    head = error.split(":", 1)[0].strip(ASCII_SPACE)
    return head if head and " " not in head else "_OTHER"


def sem_common(names, meta, out):
    if sem_string(meta.get("thread_id")):
        out[names["conversation_id"]] = meta["thread_id"]
    if sem_string(meta.get("agent_name")):
        out[names["agent_name"]] = meta["agent_name"]
    if sem_string(meta.get("run_id")):
        out["logquill.run_id"] = meta["run_id"]
    if sem_string(meta.get("node_name")):
        out["logquill.node_name"] = meta["node_name"]
    if strict_int(meta.get("retry_count")):
        out["logquill.retry_count"] = meta["retry_count"]
    error_type = sem_error_type(meta)
    if error_type:
        out["error.type"] = error_type


def sem_events(meta):
    diff = meta.get("state_diff")
    return [["logquill.state_diff", {"logquill.state": jline(diff)}]] if isinstance(diff, dict) else []


def sem_usage(names, llm, out):
    if strict_int(llm.get("tokens_in")):
        out[names["input_tokens"]] = llm["tokens_in"]
    if strict_int(llm.get("tokens_out")):
        out[names["output_tokens"]] = llm["tokens_out"]
    if sem_string(llm.get("finish_reason")):
        out[names["finish_reasons"]] = [llm["finish_reason"]]
    if strict_number(llm.get("cost_usd")):
        out["logquill.cost_usd"] = float(llm["cost_usd"])


def sem_number(value):
    return float(value) if strict_number(value) else None


def settle_semconv(data):
    op = data["op"]
    if op == "resolve":
        explicit = data.get("explicit")
        if explicit is not None:
            if explicit not in ("latest", "legacy"):
                return {"error": f"semconv must be one of latest, legacy, got {js(explicit)}"}
            name = explicit
        else:
            name = "latest"  # the opt-in token and the default both select the latest names
        return {"version": "1.44.0" if name == "latest" else "legacy", "names": dict(LATEST_NAMES if name == "latest" else LEGACY_NAMES)}
    if op == "capture":
        return {"enabled": data["env"].get(CAPTURE, "").strip(ASCII_SPACE).lower() == "true"}
    if op == "epoch":
        found = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?Z", data["timestamp"], re.ASCII)
        if not found:
            return {"ns": None}
        try:
            y, mo, d, h, mi, sec = (int(found.group(i)) for i in range(1, 7))
            whole = int(datetime.datetime(y, mo, d, h, mi, sec, tzinfo=datetime.timezone.utc).timestamp())
        except ValueError:
            return {"ns": None}
        fraction = int((found.group(7) or "").ljust(9, "0") or 0)
        return {"ns": whole * 1_000_000_000 + fraction}
    record = data["record"]
    meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
    llm = record.get("llm")
    if op == "classify":
        return {
            "span": meta.get("kind") == "span" and isinstance(meta.get("span_id"), str),
            "llm": isinstance(llm, dict),
            "tool": meta.get("kind") == "action" and sem_string(meta.get("tool")) is not None,
        }
    names = LATEST_NAMES if data.get("convention", "latest") == "latest" else LEGACY_NAMES
    if op == "log_attributes":
        if not isinstance(llm, dict):
            return {"attributes": {}}
        out = {names["operation"]: "chat"}
        if sem_string(llm.get("model")):
            out[names["request_model"]] = llm["model"]
        sem_usage(names, llm, out)
        return {"attributes": out}
    provider = data.get("default_provider", "unknown")
    if isinstance(llm, dict):
        operation = "text_completion" if meta.get("operation") == "text_completion" else "chat"
        model = sem_string(llm.get("model"))
        out = {names["operation"]: operation, names["provider"]: sem_string(meta.get("provider")) or provider}
        if model:
            out[names["request_model"]] = model
        if sem_string(meta.get("response_model")):
            out[names["response_model"]] = meta["response_model"]
        sem_usage(names, llm, out)
        if data.get("capture_content"):
            for key in ("input_messages", "output_messages"):
                if meta.get(key) is not None:
                    out[names[key]] = jline(meta[key])
        sem_common(names, meta, out)
        return {"span": {
            "name": f"{operation} {model}" if model else operation, "kind": "client", "attributes": out,
            "duration_ms": sem_number(llm.get("latency_ms")),
            "is_error": sem_error_type(meta) is not None or record.get("level") in ("ERROR", "FATAL"),
            "events": sem_events(meta),
        }}
    if meta.get("kind") == "action" and sem_string(meta.get("tool")):
        out = {names["operation"]: "execute_tool", names["tool_name"]: meta["tool"]}
        if sem_string(meta.get("tool_call_id")):
            out[names["tool_call_id"]] = meta["tool_call_id"]
        sem_common(names, meta, out)
        return {"span": {
            "name": f"execute_tool {meta['tool']}", "kind": "internal", "attributes": out,
            "duration_ms": sem_number(meta.get("duration_ms")),
            "is_error": sem_error_type(meta) is not None, "events": sem_events(meta),
        }}
    if meta.get("kind") == "span" and isinstance(meta.get("span_id"), str):
        out = {}
        sem_common(names, meta, out)
        if meta.get("operation") == "invoke_agent" or sem_string(meta.get("agent_name")):
            agent = sem_string(meta.get("agent_name")) or str(record.get("logger", ""))
            out[names["operation"]] = "invoke_agent"
            out[names["provider"]] = sem_string(meta.get("provider")) or provider
            out[names["agent_name"]] = agent
            if sem_string(meta.get("agent_id")):
                out[names["agent_id"]] = meta["agent_id"]
            name, kind = f"invoke_agent {agent}", "client"
        else:
            name, kind = str(record.get("message", "")), "internal"
        return {"span": {
            "name": name, "kind": kind, "attributes": out,
            "duration_ms": sem_number(meta.get("duration_ms")),
            "is_error": record.get("level") in ("ERROR", "FATAL"), "events": sem_events(meta),
        }}
    return {"span": None}


def settle_ecs(record):
    meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
    document = dict(record)
    if "timestamp" in record:
        document["@timestamp"] = record["timestamp"]
    if isinstance(record.get("level"), str):
        document["log"] = {"level": record["level"].lower()}
    if "logger" in record:
        document["service"] = {"name": record["logger"]}
    if meta.get("trace_id") is not None:
        document["trace"] = {"id": meta["trace_id"]}
    if meta.get("span_id") is not None:
        document["span"] = {"id": meta["span_id"]}
    if isinstance(meta.get("stack"), str):
        document["error"] = {"message": record.get("message", ""), "stack_trace": meta["stack"]}
    return {"document": document}


SQL_TYPES = {
    "sqlite": ("INTEGER PRIMARY KEY AUTOINCREMENT", "TEXT", "TEXT", "TEXT"),
    "postgres": ("SERIAL PRIMARY KEY", "TEXT", "JSONB", "JSONB"),
    "mysql": ("INT AUTO_INCREMENT PRIMARY KEY", "VARCHAR(16)", "JSON", "JSON"),
}
def settle_sql(data):
    if data["op"] == "ddl":
        key, level, meta, llm = SQL_TYPES[data.get("dialect", "sqlite")]
        return {"sql": (
            f"CREATE TABLE IF NOT EXISTS {data.get('table_name', 'logs')} ("
            f"id {key}, timestamp TEXT NOT NULL, level {level} NOT NULL, logger TEXT NOT NULL, "
            f"message TEXT NOT NULL, meta {meta} NOT NULL, llm {llm}, run_id TEXT, span_id TEXT, "
            "parent_span_id TEXT, trace_id TEXT)"
        )}
    record = data["record"]
    meta = record["meta"]

    def text(key):
        return meta.get(key) if isinstance(meta.get(key), str) else None

    return {"row": {
        "timestamp": record["timestamp"], "level": record["level"], "logger": record["logger"],
        "message": record["message"], "meta": jline(meta),
        "llm": jline(record["llm"]) if record.get("llm") else None,
        "run_id": text("run_id"), "span_id": text("span_id"),
        "parent_span_id": text("parent_span_id"), "trace_id": text("trace_id"),
    }}


def settle_graph(data, py):
    if data["op"] == "statement":
        return {"cypher": py["cypher"]}  # the statement is shared as it stands
    record, meta = data["record"], data["record"]["meta"]

    def text(key):
        value = meta.get(key)
        return value if isinstance(value, str) and value else None

    key = text("span_id") or "leaf-<random>"
    session = text(data.get("run_key", "run_id")) or text(data.get("trace_key", "trace_id"))
    parent = text("parent_span_id")
    if parent == key:
        parent = None
    props = {
        "timestamp": record["timestamp"], "level": record["level"], "logger": record["logger"],
        "message": record["message"], "meta": jline(meta),
    }
    for field in ("run_id", "trace_id", "span_id", "parent_span_id"):
        if text(field):
            props[field] = text(field)
    return {"row": {"key": key, "props": props, "session_key": session, "parent_key": parent}}


class TNode:
    def __init__(self, record):
        self.record = record
        self.children = []
        meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
        span = meta.get("span_id")
        parent = meta.get("parent_span_id")
        self.span_id = span if isinstance(span, str) and span else None
        self.parent_id = parent if isinstance(parent, str) and parent else None
        llm = record.get("llm") if isinstance(record.get("llm"), dict) else {}
        self.own = (
            llm["tokens_in"] if strict_int(llm.get("tokens_in")) else 0,
            llm["tokens_out"] if strict_int(llm.get("tokens_out")) else 0,
            float(llm["cost_usd"]) if strict_number(llm.get("cost_usd")) else 0.0,
        )
        duration = meta.get("duration_ms")
        self.duration = float(duration) if strict_number(duration) else None

    def contains(self, other):
        return other is self or any(child.contains(other) for child in self.children)

    def rollup(self):
        tin, tout, cost = self.own
        for child in self.children:
            ctin, ctout, ccost = child.rollup()
            tin, tout, cost = tin + ctin, tout + ctout, cost + ccost
        return tin, tout, cost


def tree_label(node):
    tin, tout, cost = node.rollup()
    parts = []
    if node.duration is not None:
        parts.append(f"{node.duration:,.1f}ms")
    if tin or tout:
        parts.append(f"{tin}\u2192{tout} tok")
    if cost:
        parts.append(f"${cost:,.4f}")
    note = f"  ({', '.join(parts)})" if parts else ""
    return f"[{shown(node.record.get('level'), '?')}] {shown(node.record.get('message'), '')}{note}"


def settle_tree(data):
    run_id = data["run_id"]
    nodes, pending, roots, count = {}, {}, [], 0
    for record in data["records"]:
        meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
        if meta.get("run_id") != run_id:
            continue
        count += 1
        node = TNode(record)
        if node.span_id is not None:
            node.children = pending.pop(node.span_id, [])
            nodes[node.span_id] = node
        parent = nodes.get(node.parent_id) if node.parent_id is not None else None
        if node.parent_id is None or (parent is not None and node.contains(parent)):
            roots.append(node)  # no parent, or a parent that would make a loop
        elif parent is not None:
            parent.children.append(node)
        else:
            pending.setdefault(node.parent_id, []).append(node)
    for orphans in pending.values():
        roots.extend(orphans)

    def project(node):
        tin, tout, cost = node.rollup()
        return {
            "level": node.record.get("level"), "message": node.record.get("message"),
            "rollup": {"tokens_in": tin, "tokens_out": tout, "cost_usd": cost},
            "children": [project(child) for child in node.children],
        }

    lines = []

    def walk(node, prefix, last):
        lines.append(f"{prefix}{'\u2514\u2500 ' if last else '\u251c\u2500 '}{tree_label(node)}")
        child_prefix = prefix + ("   " if last else "\u2502  ")
        for index, child in enumerate(node.children):
            walk(child, child_prefix, index == len(node.children) - 1)

    for index, root in enumerate(roots):
        walk(root, "", index == len(roots) - 1)
    return {"record_count": count, "roots": [project(root) for root in roots], "text": "\n".join(lines)}


LEVEL_WEIGHT = {"TRACE": 5, "DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40, "FATAL": 50}


def level_weight(value):
    return LEVEL_WEIGHT[value] if isinstance(value, str) else int(value)


def lkey(value):
    """A hashable stand-in for a JSON value, telling 1 from 1.0 and "1" from 1."""
    return (json_type(value), jline(value))


class Clockwork:
    """The injected clock and random draw a scenario steps through."""

    def __init__(self, data):
        self.clock = float(data.get("start", 0.0))
        self.rng = 0.0

    def advance(self, step):
        self.clock = step.get("clock", self.clock)
        self.rng = step.get("rng", self.rng)


def trim_oldest(table, limit):
    while len(table) > limit:
        table.popitem(last=False)


def settle_sampling(data):
    o = data.get("options", {})
    rate = o["rate"]
    if not 0 <= rate <= 1:
        return {"error": f"rate must be between 0 and 1, got {js(rate)}"}
    tail = o.get("tail", False)
    trace_key = o.get("trace_key", "trace_id")
    elevate_at = level_weight(o.get("elevate_at", "ERROR"))
    max_records, max_traces, max_elevated = o.get("max_buffered_records", 1000), o.get("max_traces", 200), o.get("max_elevated", 10000)
    buffer, elevated, count, time = collections.OrderedDict(), collections.OrderedDict(), 0, Clockwork(data)
    steps = []
    for step in data["steps"]:
        time.advance(step)
        record, written = step["record"], []
        trace = record["meta"].get(trace_key) if tail else None
        tkey = lkey(trace) if trace is not None else None
        if tkey is not None and tkey in elevated:
            elevated.move_to_end(tkey)
            kept = True
        else:
            keep = time.rng < rate
            if tkey is not None and level_weight(record["level"]) >= elevate_at:
                elevated[tkey] = trace
                trim_oldest(elevated, max_elevated)
                held = buffer.pop(tkey, (None, []))[1]
                count -= len(held)
                written = [r["message"] for r in held]
                kept = True
            elif keep:
                kept = True
            else:
                kept = False
                if tkey is not None:
                    if tkey in buffer:
                        buffer.move_to_end(tkey)
                    else:
                        if len(buffer) >= max_traces:
                            count -= len(buffer.popitem(last=False)[1][1])
                        buffer[tkey] = (trace, [])
                    buffer[tkey][1].append(record)
                    count += 1
                    while count > max_records and buffer:
                        count -= len(buffer.popitem(last=False)[1][1])
        steps.append({"kept": kept, "written": written})
    return {"steps": steps, "state": {
        "buffered_traces": [value for value, _ in buffer.values()],
        "buffered_records": count,
        "elevated": list(elevated.values()),
    }}


def settle_rate_limit(data):
    o = data.get("options", {})
    if o["max_records"] < 1:
        return {"error": f"max_records must be at least 1, got {js(o['max_records'])}"}
    if o["per_seconds"] <= 0:
        return {"error": f"per_seconds must be positive, got {js(o['per_seconds'])}"}
    quota, span, max_keys = o["max_records"], o["per_seconds"], o.get("max_keys", 1000)
    mode = o.get("key", "logger_level")
    windows, time, steps = collections.OrderedDict(), Clockwork(data), []
    for step in data["steps"]:
        time.advance(step)
        record = step["record"]
        key = [record["logger"], record["level"]] if mode == "logger_level" else record[mode]
        wkey = lkey(key)
        window = windows.get(wkey)
        now = time.clock
        if window is None or now - window[1] >= span:
            windows[wkey] = (key, now, 1)
            windows.move_to_end(wkey)
            trim_oldest(windows, max_keys)
            kept = True
        else:
            windows.move_to_end(wkey)
            if window[2] >= quota:
                kept = False
            else:
                windows[wkey] = (key, window[1], window[2] + 1)
                kept = True
        steps.append({"kept": kept, "written": []})
    return {"steps": steps, "state": {"windows": [[k, start, count] for k, start, count in windows.values()]}}


def settle_adaptive(data):
    o = {"base_rate": 0.1, "min_rate": 0.01, "max_rate": 1.0, "keep_at": "ERROR", "slow_ms": 1000.0,
         "max_bytes_per_second": 100000, "window_seconds": 1.0, **data.get("options", {})}
    for name in ("base_rate", "min_rate", "max_rate"):
        if not 0 <= o[name] <= 1:
            return {"error": f"{name} must be between 0 and 1, got {js(o[name])}"}
    if o["min_rate"] > o["max_rate"]:
        return {"error": f"min_rate ({js(o['min_rate'])}) must be <= max_rate ({js(o['max_rate'])})"}
    time = Clockwork(data)
    rate, window_start, demand_bytes, emitted = o["base_rate"], time.clock, 0, 0
    keep_at, budget = level_weight(o["keep_at"]), o["max_bytes_per_second"]
    steps = []
    for step in data["steps"]:
        time.advance(step)
        record = step["record"]
        if time.clock - window_start >= o["window_seconds"]:
            demand = demand_bytes / budget if budget else 0.0
            if demand < 0.5:
                rate = min(rate * 1.2, o["max_rate"])
            elif demand > 0.9:
                rate = max(rate * 0.5, o["min_rate"])
            window_start, demand_bytes, emitted = time.clock, 0, 0
        meta = record["meta"]
        duration = meta.get("duration_ms")
        always = level_weight(record["level"]) >= keep_at or (
            meta.get("kind") == "span" and strict_number(duration) and duration >= o["slow_ms"])
        if always:
            kept = True
        elif time.rng >= rate:
            kept = False
        else:
            size = len(jline(record).encode("utf-8"))
            demand_bytes += size
            if emitted + size > budget:
                kept = False
            else:
                emitted += size
                kept = True
        steps.append({"kept": kept, "written": []})
    return {"steps": steps, "state": {"rate": rate, "window_demand_bytes": demand_bytes, "window_emitted_bytes": emitted}}


def settle_flight(data):
    o = data.get("options", {})
    run_key, ship_at, flush_at = o.get("run_key", "run_id"), level_weight(o.get("ship_at", "INFO")), level_weight(o.get("flush_at", "ERROR"))
    max_records, max_runs, max_flushed = o.get("max_buffered_records", 1000), o.get("max_runs", 200), o.get("max_flushed", 10000)
    buffer, flushed, count, steps = collections.OrderedDict(), collections.OrderedDict(), 0, []
    for step in data["steps"]:
        record, written = step["record"], []
        run = record["meta"].get(run_key)
        rkey = lkey(run) if run is not None else None
        if rkey is None:
            kept = True
        elif rkey in flushed:
            flushed.move_to_end(rkey)
            kept = True
        else:
            weight = level_weight(record["level"])
            if weight >= flush_at:
                flushed[rkey] = run
                trim_oldest(flushed, max_flushed)
                held = buffer.pop(rkey, (None, []))[1]
                count -= len(held)
                written = [r["message"] for r in held]
                kept = True
            elif weight >= ship_at:
                kept = True
            else:
                kept = False
                if rkey in buffer:
                    buffer.move_to_end(rkey)
                else:
                    if len(buffer) >= max_runs:
                        count -= len(buffer.popitem(last=False)[1][1])
                    buffer[rkey] = (run, [])
                buffer[rkey][1].append(record)
                count += 1
                while count > max_records and buffer:
                    count -= len(buffer.popitem(last=False)[1][1])
        steps.append({"kept": kept, "written": written})
    return {"steps": steps, "state": {
        "buffered_runs": [value for value, _ in buffer.values()],
        "buffered_records": count,
        "flushed": list(flushed.values()),
    }}


def settle_run_summary(data):
    o = data.get("options", {})
    run_key, logger_name, max_runs = o.get("run_key", "run_id"), o.get("logger_name", "app.run_summary"), o.get("max_runs", 1000)
    stats, steps = collections.OrderedDict(), []
    for step in data["steps"]:
        record, written = step["record"], []
        meta = record["meta"]
        run = meta.get(run_key)
        if run is not None:
            rkey = lkey(run)
            entry = stats.get(rkey)
            if entry is None:
                if len(stats) >= max_runs:
                    stats.popitem(last=False)
                entry = stats[rkey] = {"run": run, "tin": 0, "tout": 0, "cost": 0.0, "tools": 0, "retries": 0, "errors": 0, "records": 0}
            else:
                stats.move_to_end(rkey)
            entry["records"] += 1
            if level_weight(record["level"]) >= 40:
                entry["errors"] += 1
            if strict_int(meta.get("retry_count")):
                entry["retries"] += meta["retry_count"]
            if meta.get("kind") == "action" and isinstance(meta.get("tool"), str):
                entry["tools"] += 1
            llm = record.get("llm") if isinstance(record.get("llm"), dict) else None
            if llm:
                if strict_int(llm.get("tokens_in")):
                    entry["tin"] += llm["tokens_in"]
                if strict_int(llm.get("tokens_out")):
                    entry["tout"] += llm["tokens_out"]
                if strict_number(llm.get("cost_usd")):
                    entry["cost"] += llm["cost_usd"]
            if meta.get("kind") == "span" and isinstance(meta.get("span_id"), str) and "parent_span_id" not in meta:
                del stats[rkey]
                summary = {"kind": "run_summary", run_key: run, "tokens_in": entry["tin"], "tokens_out": entry["tout"],
                           "cost_usd": entry["cost"], "tool_calls": entry["tools"], "retries": entry["retries"],
                           "errors": entry["errors"], "record_count": entry["records"]}
                if strict_number(meta.get("duration_ms")):
                    summary["duration_ms"] = float(meta["duration_ms"])
                summary["parent_span_id"] = meta["span_id"]
                written = [{"schema_version": "2.0", "level": "INFO", "logger": logger_name, "message": "run summary", "meta": summary}]
        steps.append({"written": written})
    return {"steps": steps, "state": {"open_runs": [entry["run"] for entry in stats.values()]}}


def settle_run_plugin(data):
    given = data.get("options", {}).get("run_id")
    run_id = given or "<generated>"
    steps = []
    for number, step in enumerate(data["steps"]):
        meta = dict(step["record"]["meta"])
        meta.setdefault("run_id", run_id)
        meta["step"] = number
        steps.append({"kept": True, "meta": meta, "written": []})
    return {"steps": steps, "state": {"step": len(steps)}}


def settle_alerts(data):
    o = {"threshold": "ERROR", "dedupe_window_seconds": 300.0, "max_tracked_keys": 500, **data.get("options", {})}
    threshold, window_len, limit = level_weight(o["threshold"]), o["dedupe_window_seconds"], o["max_tracked_keys"]
    by_message = o.get("key") == "message"
    windows, steps = collections.OrderedDict(), []

    def fire(until, sent):
        due = sorted((item for item in windows.items() if item[1]["due"] <= until), key=lambda item: item[1]["due"])
        for key, window in due:
            del windows[key]
            if window["count"] > 1:
                sent.append({"message": window["message"], "occurrences": window["count"]})

    for step in data["steps"]:
        sent = []
        fire(step["at"], sent)
        record = step.get("record")
        if record is not None and level_weight(record["level"]) >= threshold:
            key = record["message"] if by_message else f"{record['level']}:{record['logger']}:{record['message']}"
            if key in windows:
                windows[key]["count"] += 1
            else:
                if len(windows) < limit:
                    windows[key] = {"message": record["message"], "count": 1, "due": step["at"] + window_len}
                sent.append({"message": record["message"], "occurrences": 1})
        steps.append({"sent": sent})
    after = []
    if "end" in data:
        fire(data["end"], after)
    elif data.get("close"):
        windows.clear()
    return {"steps": steps, "after": after, "tracked": len(windows)}


ASCII_SPACE = " \t\n\r\x0b\x0c"


def settle_trace_header(header):
    text = header.strip(ASCII_SPACE)
    found = re.fullmatch(r"([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}", text, re.ASCII)
    if found:
        version, trace, parent = found.groups()
        ok = version != "ff" and int(trace, 16) != 0 and int(parent, 16) != 0
        return {"trace_id": trace if ok else None}
    found = re.search(r"Root=1-([0-9a-f]{8})-([0-9a-f]{24})", text, re.ASCII)
    if found:
        trace = found.group(1) + found.group(2)
        return {"trace_id": trace if int(trace, 16) != 0 else None}
    found = re.fullmatch(r"([0-9a-f]{32})/[0-9]+(;o=[0-9])?", text, re.ASCII)
    if found and int(found.group(1), 16) != 0:
        return {"trace_id": found.group(1)}
    return {"trace_id": None}


def digest_hex(text, nbytes):
    raw = hashlib.sha256(text.encode("utf-8")).digest()[:nbytes]
    return raw.hex() if int.from_bytes(raw, "big") else "0" * (nbytes * 2 - 1) + "1"


def settle_id(data):
    op = data["op"]
    if op == "span_id":
        value = data["value"]
        if re.fullmatch(r"[0-9a-f]{16}", value, re.ASCII) and int(value, 16) != 0:
            return {"id": value}
        return {"id": digest_hex(value, 8)}
    if op == "trace_id":
        meta = data["meta"]
        trace = meta.get("trace_id")
        if isinstance(trace, str) and re.fullmatch(r"[0-9a-f]{32}", trace, re.ASCII) and int(trace, 16) != 0:
            return {"id": trace}
        run = meta.get("run_id")
        if isinstance(run, str) and run:
            return {"id": digest_hex("run:" + run, 16)}
        return {"id": None}
    return {"id": digest_hex("parent:" + data["value"], 16)}


# --------------------------------------------------------------------- groups

def comparable_chain(out):
    """The part of a hash-chain result both references produce."""
    keep = {"chain": out["chain"], "head_hash": out["head_hash"], "verify": {"ok": out["verify"]["ok"]}}
    if "signature" in out:
        keep["signature"] = out["signature"]
    return keep


def classify_canonical(case):
    text = case["input"]["json"]
    if "astral" in case["name"]:
        return "03 — keys sort by Unicode code point (JS sorts by UTF-16 unit)"
    if re.search(r"NaN|Infinity|1e999", text):
        return "D19 — NaN and Infinity print as those words in the canonical text (JS prints null)"
    if re.search(r"(?<![.\d])\d{16,}(?![.\d])", text):
        return "D4 — integers keep every digit (JS numbers lose precision above 2^53)"
    return "D4 — a float keeps its .0 and follows Python's repr; JS cannot tell 12.0 from 12"


def settle_case(group, case, py, other):
    """Returns (expected, rule) for one case; `rule` explains any difference from a reference."""
    data = case["input"]
    if group == "canonical-json":
        return py, classify_canonical(case)
    if group == "hash-chain":
        return py, "D4 — a float keeps its .0, so the canonical text and every later hash differ"
    if group == "content-policy":
        if "error_policy" in data:
            text = f'content policy must be one of "off", "hash", "truncate", "full", got {js(data["error_policy"])}'
            return {"error": text}, "D37 — one error text in the core; bindings may name their own option"
        if data["policy"] == "off":
            return {"value": PLACEHOLDER}, "D3 — one neutral placeholder for every language"
        return py, "D4 — a float keeps its .0 in the canonical text"
    if group == "field-classes":
        return py, "03 — the registry is the same in both languages"
    if group == "levels":
        return settle_level(data["value"]), "D35 — ASCII-only case folding; a type error for values that are neither a name nor a number"
    scenarios = {
        "sampling": settle_sampling, "rate-limit": settle_rate_limit, "adaptive-sampling": settle_adaptive,
        "flight-recorder": settle_flight, "run-summary": settle_run_summary, "run-plugin": settle_run_plugin,
        "alert-dedupe": settle_alerts,
    }
    if group in scenarios:
        return scenarios[group](data), "09 — D24 tail and flight-recorder sets are bounded; D36 messages show JSON values and wire names; D44 a full alert table never drops an alert; D5 run ids are 32 hex digits"
    if group == "trace-tree":
        return settle_tree(data), "06 — a record that would become its own ancestor is a root, never dropped; a missing message or level prints as an empty message or `?`"
    if group == "sql-rows":
        return settle_sql(data), "D33 — an llm column beside meta, so a stored record reads back whole; D32 — one UTF-8 JSON spelling for meta; one table definition per dialect"
    if group == "graph-rows":
        return settle_graph(data, py), "09 — graph rows exist in Python only; every binding reaches them"
    if group == "ecs":
        return settle_ecs(data["record"]), "03 — ECS fields ride beside LogQuill's own; a record with no or null meta is mapped, not rejected"
    if group == "semconv":
        return settle_semconv(data), "03 — attributes only for integer counts (not booleans or floats) and numeric costs (not booleans); one JSON spelling for event text (D32); timestamps parsed exactly, not through a float; one error text (D36/D37)"
    if group == "formatters":
        if data["op"] == "parse_logfmt":
            return settle_parse_logfmt(data["line"]), "D42 — ASCII whitespace only; a lone surrogate escape stays as text; a valid pair is the character"
        record = data["record"] if "record" in data else json.loads(data["record_json"])
        return settle_format(data["op"], record), "D4 — floats keep Python's spelling; D31 — non-finite numbers are null; D32 — UTF-8, not \\\\u escapes; D42 — one tolerant treatment of incomplete records and ASCII whitespace in logfmt"
    if group == "redaction":
        return settle_redaction(data), "D6 — all 12 patterns; D27 — keys are checked at every depth; D41 — ASCII \\b \\d \\s, and a value deeper than 50 levels is replaced, not left alone"
    if group == "trace-headers":
        return settle_trace_header(data["header"]), "D40 — ASCII-only matching and trimming; an all-zero id or version ff is not a trace"
    if group == "otel-ids":
        return settle_id(data), "D40 — ids match `^[0-9a-f]{n}$` strictly: no trailing newline, no non-ASCII digits"
    if data["op"] == "timestamp":
        return py, None
    if data["op"] == "llm_block":
        return settle_llm(data["fields"]), "D36 — warnings read `llm_call:` plus the field's JSON value; D4 — a JSON 10.0 is a float, not an integer"
    return settle_parse(data["record"]), "D36 — values in messages are JSON text; D38 — versions are ASCII digits; D39 — a null llm is absent; D18 — 1.x camelCase meta keys are renamed"


def build_group(group, py_out, js_out, spec, unsupported):
    cases, unsettled = [], []
    for case, py, other in zip(spec["cases"], py_out, js_out):
        expected, rule = settle_case(group, case, py, other)
        item = {"name": case["name"], "input": case["input"], "expected": expected}
        differs = {}
        if isinstance(other, dict) and "unsupported" in other:
            unsupported.add(other["unsupported"])
            other = expected  # a missing feature is a gap to close, recorded once per group
        if group == "hash-chain":
            if not same(comparable_chain(py), other):
                differs["js"] = other
        elif group in SCENARIO_GROUPS:
            if not same(py, expected):
                differs["python"] = py
            seen = {k: v for k, v in expected.items() if k in other}
            if not same(seen, other):
                differs["js"] = other
        else:
            if not same(py, expected):
                differs["python"] = py
            if not same(other, expected):
                differs["js"] = other
        if differs:
            if rule is None:
                unsettled.append(case["name"])
            item["differs"] = scrub(differs)
            item["rule"] = rule
        cases.append(item)
    if unsettled:
        raise Unsettled(f"{group}: no decision explains {unsettled}")
    return cases


def render(group, tree_py, tree_js):
    spec = json.loads((HERE / "inputs" / f"{group}.json").read_text(encoding="utf-8"))
    py_out = run_reference("python", group, tree_py)
    js_out = run_reference("js", group, tree_js)
    unsupported = set()
    cases = build_group(group, py_out, js_out, spec, unsupported)
    document = {
        "group": group,
        "description": spec["description"],
        "references": {"python": "2.0.0", "js": js_commit(tree_js)},
    }
    if unsupported:
        document["js_gaps"] = sorted(unsupported)
    document["cases"] = cases
    return json.dumps(document, indent=2, ensure_ascii=True) + "\n"


def strip_references(text):
    return re.sub(r'"js": "[0-9a-z]+"', '"js": "-"', text, count=1)


def report():
    """A Markdown account of every divergence recorded in the committed corpus."""
    lines = [
        "# Where the old releases disagree with the settled behaviour",
        "",
        "Generated by `python3 scripts/corpus/build.py --report` from `schema/corpus/`; do not edit.",
        "Each group lists the cases where the Python 2.0.0 or JavaScript reference printed something",
        "other than the expected output, and the decision that settled it. Cases that are not listed",
        "agree in all three.",
        "",
        "| Group | Cases | Python differs | JavaScript differs |",
        "|---|---:|---:|---:|",
    ]
    details = []
    for group in GROUPS:
        document = json.loads((CORPUS / f"{group}.json").read_text(encoding="utf-8"))
        cases = document["cases"]
        py = [c for c in cases if "python" in c.get("differs", {})]
        other = [c for c in cases if "js" in c.get("differs", {})]
        lines.append(f"| `{group}` | {len(cases)} | {len(py)} | {len(other)} |")
        block = [f"## `{group}`", "", document["description"], ""]
        if "js_gaps" in document:
            block += [f"JavaScript gap: {'; '.join(document['js_gaps'])}.", ""]
        rules = {c["rule"] for c in cases if "differs" in c}
        for rule in sorted(rules):
            block += [f"**Rule:** {rule}", ""]
            for label, key in (("Python", "python"), ("JavaScript", "js")):
                names = [c["name"] for c in cases if c.get("rule") == rule and key in c.get("differs", {})]
                if names:
                    block.append(f"- {label} ({len(names)}): " + "; ".join(names))
            block.append("")
        details.append("\n".join(block))
    return ("\n".join(lines) + "\n\n" + "\n".join(details)).rstrip("\n") + "\n"


def main(argv):
    if "--report" in argv:
        sys.stdout.write(report())
        return
    check = "--check" in argv
    names = [a for a in argv if not a.startswith("-")] or GROUPS
    tree_py = python_tree()
    tree_js = os.environ.get("LOGQUILL_JS_DIR", str((ROOT.parent / "logquill-js").resolve()))
    CORPUS.mkdir(parents=True, exist_ok=True)
    stale = []
    for group in names:
        text = render(group, tree_py, tree_js)
        target = CORPUS / f"{group}.json"
        if check:
            current = target.read_text(encoding="utf-8") if target.exists() else ""
            if strip_references(current) != strip_references(text):
                stale.append(group)
        else:
            target.write_text(text, encoding="utf-8")
            print(f"wrote {target.relative_to(ROOT)} ({text.count(chr(10))} lines)")
    if stale:
        raise SystemExit(f"out of date: {', '.join(stale)} — run scripts/corpus/build.py and review the diff")
    if check:
        print("corpus is up to date")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Unsettled as error:
        raise SystemExit(f"UNSETTLED: {error}")
