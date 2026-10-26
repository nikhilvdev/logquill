"""Runs the Python 2.0.0 reference on one corpus inputs file and prints one output per case.

Usage: LOGQUILL_PY_DIR=<unpacked v2.0.0 tree> python3 -I ref_py.py inputs/<group>.json

Outputs are plain JSON; build.py compares them with the JavaScript reference and applies the
settled decisions. Nothing here decides what is right.
"""

import copy
import hashlib
import json
import logging
import os
import sys

sys.path.insert(0, os.environ["LOGQUILL_PY_DIR"])

import logquill.core.records as records  # noqa: E402
from logquill.core.levels import parse_level  # noqa: E402
from logquill.plugins import tamper_evident_plugin as tamper  # noqa: E402
from logquill.formatters.json_formatter import JSONFormatter  # noqa: E402
from logquill.formatters.logfmt_formatter import LogfmtFormatter  # noqa: E402
from logquill.formatters.text_formatter import TextFormatter  # noqa: E402
from logquill.parsing import parse_logfmt  # noqa: E402
from logquill.tracing import semconv  # noqa: E402
from logquill.transports.cloud.ecs_mapper import to_ecs_document  # noqa: E402
from logquill.transports.graph import base_graph_transport as graph_base  # noqa: E402
from logquill.transports.sql.mysql_transport import MySQLTransport  # noqa: E402
from logquill.transports.sql.postgres_transport import PostgresTransport  # noqa: E402
from logquill.transports.sql.sqlite_transport import SQLiteTransport  # noqa: E402
from logquill.cli import trace_tree  # noqa: E402
from logquill.plugins import alerting_plugin as alerting_module  # noqa: E402
from logquill.plugins.adaptive_sampling_plugin import AdaptiveSamplingPlugin  # noqa: E402
from logquill.plugins.alerting_plugin import AlertingPlugin  # noqa: E402
from logquill.plugins.flight_recorder_plugin import FlightRecorderPlugin  # noqa: E402
from logquill.plugins.rate_limit_plugin import RateLimitPlugin  # noqa: E402
from logquill.plugins.run_plugin import RunPlugin  # noqa: E402
from logquill.plugins.run_summary_plugin import RunSummaryPlugin  # noqa: E402
from logquill.plugins.sampling_plugin import SamplingPlugin  # noqa: E402
from logquill.plugins.trace_context_plugin import parse_trace_header  # noqa: E402
from logquill.plugins.pii_redact_plugin import PIIRedactPlugin  # noqa: E402
from logquill.plugins.redact_plugin import DEFAULT_REDACTED_KEYS, RedactPlugin  # noqa: E402
from logquill.privacy import privacy  # noqa: E402
from logquill.transports.otel import _ids  # noqa: E402


def canonical_json(case):
    value = json.loads(case["json"])
    text = privacy._to_text(value)
    return {"text": text, "sha256": hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()}


def result_dict(result):
    return {
        "ok": result.ok,
        "records_checked": result.records_checked,
        "broken_at": result.broken_at,
        "reason": result.reason,
        "head_hash": result.head_hash,
    }


def mutate(chain, mutations):
    out = copy.deepcopy(chain)
    for step in mutations:
        kind = step["type"]
        if kind == "edit_message":
            out[step["index"]]["message"] = step["value"]
        elif kind == "edit_meta":
            out[step["index"]]["meta"][step["key"]] = step["value"]
        elif kind == "edit_level":
            out[step["index"]]["level"] = step["value"]
        elif kind == "edit_llm":
            out[step["index"]]["llm"][step["key"]] = step["value"]
        elif kind == "edit_schema_version":
            out[step["index"]]["schema_version"] = step["value"]
        elif kind == "edit_hash":
            out[step["index"]]["meta"]["hash"] = step["value"]
        elif kind == "edit_prev_hash":
            out[step["index"]]["meta"]["prev_hash"] = step["value"]
        elif kind == "drop_hash":
            del out[step["index"]]["meta"]["hash"]
        elif kind == "delete":
            del out[step["index"]]
        elif kind == "swap":
            a, b = step["index"], step["other"]
            out[a], out[b] = out[b], out[a]
        elif kind == "truncate":
            out = out[: step["keep"]]
        else:
            raise ValueError(kind)
    return out


def hash_chain(case):
    genesis = case.get("genesis", tamper.GENESIS_HASH)
    plugin = tamper.TamperEvidentPlugin(genesis_hash=genesis)
    chain = [plugin.before_log(copy.deepcopy(record)) for record in case["records"]]
    out = {"chain": chain, "head_hash": plugin.head_hash}
    key = case.get("key")
    signature = None
    if key is not None:
        signature = plugin.sign_head(key.encode("utf-8"))
        out["signature"] = signature
    mutated = mutate(chain, case.get("mutations", []))
    out["verify"] = result_dict(tamper.verify_chain_detailed(mutated, genesis_hash=genesis))
    if signature is not None:
        out["verify_signed"] = result_dict(
            tamper.verify_signed_chain(
                mutated, key=key.encode("utf-8"), signature=signature, genesis_hash=genesis
            )
        )
    return out


def content_policy(case):
    if "error_policy" in case:
        try:
            privacy.parse_content_policy(case["error_policy"])
        except ValueError as error:
            return {"error": str(error)}
        return {"ok": True}
    meta = {case.get("field", "input_messages"): json.loads(case["json"])}
    privacy.apply_content_policy(meta, case["policy"])
    return {"value": next(iter(meta.values()))}


def field_classes(case):
    return {
        "registry": dict(privacy.FIELD_CLASSES),
        "content_fields": list(privacy.CONTENT_FIELDS),
        "keys_in_class": {
            name: sorted(privacy.keys_in_class(name)) for name in ("secret", "pii", "content")
        },
    }


def levels(case):
    value = case["value"]
    try:
        return {"level": parse_level(value).name}
    except (ValueError, TypeError) as error:
        return {"error": str(error)}


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def records_case(case):
    op = case["op"]
    if op == "timestamp":
        micros = case["unix_micros"]
        records.time.time = lambda: micros / 1_000_000  # type: ignore[attr-defined]
        records._second_cache = (-1, "")
        return {"timestamp": records.utc_timestamp()}
    if op == "llm_block":
        handler = _Capture()
        logger = logging.getLogger("logquill")
        logger.addHandler(handler)
        try:
            block = records.build_llm_block(**case["fields"])
        finally:
            logger.removeHandler(handler)
        return {"block": block, "warnings": handler.messages}
    if op == "parse_record":
        try:
            parsed = records.parse_record(case["record"])
        except ValueError as error:
            return {"error": str(error)}
        return {"record": parsed}
    raise ValueError(op)


def trace_headers(case):
    return {"trace_id": parse_trace_header(case["header"])}


def otel_ids(case):
    op = case["op"]
    if op == "span_id":
        return {"id": format(_ids.span_id_for(case["value"]), "016x")}
    if op == "trace_id":
        found = _ids.trace_id_for(case["meta"])
        return {"id": None if found is None else format(found, "032x")}
    return {"id": format(_ids.trace_id_from_parent(case["value"]), "032x")}


def redaction(case):
    op = case["op"]
    replacement = case.get("replacement", "***")
    if op in ("redact", "redact_local"):
        plugin = RedactPlugin(
            keys=case.get("keys", DEFAULT_REDACTED_KEYS),
            replacement=replacement,
            classes=case.get("classes"),
        )
    else:
        plugin = PIIRedactPlugin(replacement=replacement)
    try:
        if op in ("redact", "pii"):
            return {"meta": plugin.before_log({"meta": copy.deepcopy(case["meta"])})["meta"]}
        return {"text": plugin.redact_local(case.get("name", "x"), case["text"])}
    except Exception as error:  # a plugin that raises is part of what the corpus records
        return {"error": f"{type(error).__name__}: {error}"}


def formatters(case):
    op = case["op"]
    if op == "parse_logfmt":
        return {"fields": parse_logfmt(case["line"])}
    record = json.loads(case["record_json"]) if "record_json" in case else case["record"]
    formatter = {"json": JSONFormatter, "text": TextFormatter, "logfmt": LogfmtFormatter}[op]()
    try:
        return {"line": formatter.format(record)}
    except Exception as error:
        return {"error": f"{type(error).__name__}: {error}"}


def ecs(case):
    try:
        return {"document": to_ecs_document(copy.deepcopy(case["record"]))}
    except Exception as error:
        return {"error": f"{type(error).__name__}: {error}"}


def spec_dict(spec):
    return {
        "name": spec.name,
        "kind": spec.kind,
        "attributes": spec.attributes,
        "duration_ms": spec.duration_ms,
        "is_error": spec.is_error,
        "events": [[name, attrs] for name, attrs in spec.events],
    }


def semconv_case(case):
    op = case["op"]
    try:
        if op == "resolve":
            found = semconv.resolve_convention(case.get("explicit"), case.get("env", {}))
            return {"version": found.version, "names": dict(found.names)}
        if op == "capture":
            return {"enabled": semconv.capture_content_enabled(case["env"])}
        if op == "epoch":
            try:
                return {"ns": semconv.epoch_ns(case["timestamp"])}
            except ValueError:
                return {"ns": None}
        record = copy.deepcopy(case["record"])
        if op == "classify":
            return {
                "span": semconv.is_span_record(record),
                "llm": semconv.is_llm_record(record),
                "tool": semconv.is_tool_record(record),
            }
        convention = semconv.resolve_convention(case.get("convention", "latest"), {})
        if op == "log_attributes":
            return {"attributes": semconv.log_attributes(record, convention)}
        provider = case.get("default_provider", "unknown")
        if semconv.is_llm_record(record):
            spec = semconv.llm_spec(record, convention, default_provider=provider, capture_content=case.get("capture_content", False))
        elif semconv.is_tool_record(record):
            spec = semconv.tool_spec(record, convention)
        elif semconv.is_span_record(record):
            spec = semconv.span_spec(record, convention, default_provider=provider)
        else:
            return {"span": None}
        return {"span": spec_dict(spec)}
    except Exception as error:
        return {"error": f"{type(error).__name__}: {error}"}


SQL_CLASSES = {"sqlite": SQLiteTransport, "postgres": PostgresTransport, "mysql": MySQLTransport}


def sql_rows(case):
    try:
        transport = SQL_CLASSES[case.get("dialect", "sqlite")](
            connection=object(), table_name=case.get("table_name", "logs")
        )
        if case["op"] == "ddl":
            return {"sql": transport.create_table_sql()}
        return {"row": dict(transport._to_item("", copy.deepcopy(case["record"])))}
    except Exception as error:
        return {"error": f"{type(error).__name__}: {error}"}


class _Graph(graph_base.BaseGraphTransport):
    def _execute(self, query, parameters):
        pass


def graph_rows(case):
    if case["op"] == "statement":
        return {"cypher": graph_base.CYPHER}
    try:
        options = {k: case[k] for k in ("run_key", "trace_key") if k in case}
        row = _Graph(**options)._to_item("", copy.deepcopy(case["record"]))
        key = row.key
        if key.startswith("leaf-"):
            key = "leaf-<random>"
        return {"row": {"key": key, "props": row.props, "session_key": row.session_key, "parent_key": row.parent_key}}
    except Exception as error:
        return {"error": f"{type(error).__name__}: {error}"}


def tree_node(node):
    rollup = node.rollup()
    return {
        "level": node.record.get("level"),
        "message": node.record.get("message"),
        "rollup": {"tokens_in": rollup.tokens_in, "tokens_out": rollup.tokens_out, "cost_usd": rollup.cost_usd},
        "children": [tree_node(child) for child in node.children],
    }


def trace_tree_case(case):
    try:
        builder = trace_tree.build_trace(copy.deepcopy(case["records"]), case["run_id"])
        return {
            "record_count": builder.record_count,
            "roots": [tree_node(root) for root in builder.roots],
            "text": trace_tree.render_tree(builder.roots),
        }
    except RecursionError:
        return {"error": "RecursionError"}
    except Exception as error:
        return {"error": f"{type(error).__name__}: {error}"}


class FakeTransport:
    """Collects what a plugin writes straight to its transports."""

    def __init__(self, sink):
        self.sink = sink

    def format(self, record):
        return record["message"]

    def write(self, formatted, record):
        self.sink.append(copy.deepcopy(record))


KEY_FUNCS = {
    "logger_level": None,
    "logger": lambda record: record["logger"],
    "message": lambda record: record["message"],
}


def listify(value):
    return [listify(v) for v in value] if isinstance(value, tuple) else value


def build_sampling(opts, state, written):
    options = {k: opts[k] for k in ("trace_key", "elevate_at", "max_buffered_records", "max_traces") if k in opts}
    transports = [FakeTransport(written)] if opts.get("tail") else None
    return SamplingPlugin(opts["rate"], rng=lambda: state["rng"], transports=transports, **options)


def state_sampling(plugin):
    return {
        "buffered_traces": listify(list(plugin._buffer)),
        "buffered_records": plugin._buffered_count,
        "elevated": listify(list(plugin._elevated)),
    }


def build_rate_limit(opts, state, written):
    options = {"max_keys": opts["max_keys"]} if "max_keys" in opts else {}
    key = KEY_FUNCS[opts.get("key", "logger_level")]
    if key is not None:
        options["key_func"] = key
    return RateLimitPlugin(opts["max_records"], opts["per_seconds"], clock=lambda: state["clock"], **options)


def state_rate_limit(plugin):
    return {"windows": [[listify(key), start, count] for key, (start, count) in plugin._windows.items()]}


def build_adaptive(opts, state, written):
    return AdaptiveSamplingPlugin(**opts, rng=lambda: state["rng"], clock=lambda: state["clock"])


def state_adaptive(plugin):
    return {"rate": plugin.rate, "window_demand_bytes": plugin._window_demand_bytes, "window_emitted_bytes": plugin._window_emitted_bytes}


def build_flight(opts, state, written):
    known = ("run_key", "ship_at", "flush_at", "max_buffered_records", "max_runs")
    return FlightRecorderPlugin(transports=[FakeTransport(written)], **{k: v for k, v in opts.items() if k in known})


def state_flight(plugin):
    return {"buffered_runs": listify(list(plugin._buffer)), "buffered_records": plugin._buffered_count, "flushed": listify(list(plugin._flushed))}


def build_run_summary(opts, state, written):
    return RunSummaryPlugin(transports=[FakeTransport(written)], **opts)


def state_run_summary(plugin):
    return {"open_runs": listify(list(plugin._stats))}


def build_run_plugin(opts, state, written):
    return RunPlugin(opts.get("run_id"))


def state_run_plugin(plugin):
    return {"step": plugin._step}


PLUGINS = {
    "sampling": (build_sampling, state_sampling, "before"),
    "rate-limit": (build_rate_limit, state_rate_limit, "before"),
    "adaptive-sampling": (build_adaptive, state_adaptive, "before"),
    "flight-recorder": (build_flight, state_flight, "before"),
    "run-summary": (build_run_summary, state_run_summary, "after"),
    "run-plugin": (build_run_plugin, state_run_plugin, "before"),
}


def scenario(group, case):
    build, state_of, hook = PLUGINS[group]
    state = {"clock": float(case.get("start", 0.0)), "rng": 0.0}
    written = []
    try:
        plugin = build(copy.deepcopy(case.get("options", {})), state, written)
    except (ValueError, TypeError) as error:
        return {"error": str(error)}
    steps = []
    for step in case["steps"]:
        state["clock"] = step.get("clock", state["clock"])
        state["rng"] = step.get("rng", state["rng"])
        written.clear()
        record = copy.deepcopy(step["record"])
        out = {}
        if hook == "before":
            result = plugin.before_log(record)
            out["kept"] = result is not None
            if group == "run-plugin" and result is not None:
                meta = dict(result["meta"])
                if not case.get("options", {}).get("run_id") and meta.get("run_id") == plugin.run_id:
                    meta["run_id"] = "<generated>"
                out["meta"] = meta
        else:
            plugin.after_log(record)
        if group == "run-summary":
            out["written"] = [{k: v for k, v in w.items() if k != "timestamp"} for w in written]
        else:
            out["written"] = [w["message"] for w in written]
        steps.append(out)
    return {"steps": steps, "state": state_of(plugin)}


class FakeTimer:
    now = 0.0
    pending = []

    def __init__(self, interval, function, args=None, kwargs=None):
        self.interval, self.function, self.args = interval, function, tuple(args or ())
        self.daemon = False
        self.cancelled = False
        self.due = None

    def start(self):
        self.due = FakeTimer.now + self.interval
        FakeTimer.pending.append(self)

    def cancel(self):
        self.cancelled = True


class FakeThread:
    def __init__(self, target=None, args=(), daemon=None, **_):
        self.target, self.args = target, tuple(args)

    def start(self):
        self.target(*self.args)


def fire_due(until):
    while True:
        due = sorted((t for t in FakeTimer.pending if not t.cancelled and t.due <= until), key=lambda t: t.due)
        if not due:
            return
        timer = due[0]
        FakeTimer.pending.remove(timer)
        FakeTimer.now = timer.due
        timer.function(*timer.args)


def alert_dedupe(case):
    sent = []
    opts = copy.deepcopy(case.get("options", {}))
    key = opts.pop("key", None)
    if key == "message":
        opts["dedupe_key"] = lambda record: record["message"]

    class Capture(AlertingPlugin):
        def send_alert(self, record, occurrences):
            sent.append({"message": record["message"], "occurrences": occurrences})

    real_timer, real_thread = alerting_module.threading.Timer, alerting_module.threading.Thread
    alerting_module.threading.Timer, alerting_module.threading.Thread = FakeTimer, FakeThread
    FakeTimer.now, FakeTimer.pending = 0.0, []
    try:
        plugin = Capture(**opts)
        steps = []
        for step in case["steps"]:
            sent.clear()
            fire_due(step["at"])
            FakeTimer.now = step["at"]
            if "record" in step:
                plugin.after_log(copy.deepcopy(step["record"]))
            steps.append({"sent": list(sent)})
        sent.clear()
        if "end" in case:
            fire_due(case["end"])
        elif case.get("close"):
            plugin.close()
        return {"steps": steps, "after": list(sent), "tracked": len(plugin._windows)}
    except (ValueError, TypeError) as error:
        return {"error": str(error)}
    finally:
        alerting_module.threading.Timer, alerting_module.threading.Thread = real_timer, real_thread


GROUPS = {
    "sampling": lambda case: scenario("sampling", case),
    "rate-limit": lambda case: scenario("rate-limit", case),
    "adaptive-sampling": lambda case: scenario("adaptive-sampling", case),
    "flight-recorder": lambda case: scenario("flight-recorder", case),
    "run-summary": lambda case: scenario("run-summary", case),
    "run-plugin": lambda case: scenario("run-plugin", case),
    "alert-dedupe": alert_dedupe,
    "trace-tree": trace_tree_case,
    "sql-rows": sql_rows,
    "graph-rows": graph_rows,
    "ecs": ecs,
    "semconv": semconv_case,
    "formatters": formatters,
    "redaction": redaction,
    "trace-headers": trace_headers,
    "otel-ids": otel_ids,
    "canonical-json": canonical_json,
    "hash-chain": hash_chain,
    "content-policy": content_policy,
    "field-classes": field_classes,
    "levels": levels,
    "records": records_case,
}

if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as handle:
        spec = json.load(handle)
    run = GROUPS[spec["group"]]
    json.dump([run(case["input"]) for case in spec["cases"]], sys.stdout)
