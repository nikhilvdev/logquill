"""Scenarios that run unchanged under logquill 2.0.0 and under this version.

`run_all.py` runs every scenario in one interpreter and prints one JSON document; `test_oracle.py`
runs it under both and compares. Only the public API is used, so a scenario is also a statement of
what the two versions must agree on. Anything volatile (timestamps, generated ids, durations) is
masked by `mask` so that what remains is behaviour.
"""

from __future__ import annotations

import json
import re
import threading

from logquill import (
    CollectingTransport,
    Logger,
    RunPlugin,
    bind_context,
    current_context,
    disable,
    enable,
)

SCENARIOS = {}


def scenario(fn):
    SCENARIOS[fn.__name__] = fn
    return fn


_HEX = re.compile(r"^[0-9a-f]{16}$|^[0-9a-f]{32}$")


def mask(value, ids=None):
    """The value with timestamps, generated ids and timings replaced by stable placeholders."""
    ids = {} if ids is None else ids
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "timestamp":
                out[key] = "<ts>"
            elif key in ("duration_ms", "latency_ms") and isinstance(item, float):
                out[key] = "<ms>"
            elif key in ("hash", "prev_hash", "head"):
                out[key] = "<hash>"
            else:
                out[key] = mask(item, ids)
        return out
    if isinstance(value, (list, tuple)):
        return [mask(item, ids) for item in value]
    if isinstance(value, str) and _HEX.match(value):
        return ids.setdefault(value, f"<id{len(ids) + 1}>")
    if isinstance(value, str) and value.startswith("<content capture is off"):
        return "<content capture is off>"  # decision D3 changed the wording, not the behaviour
    return value


def records(logger_factory, work):
    """Runs `work(logger)` against a collecting transport and returns the records it saw."""
    sink = CollectingTransport()
    logger = logger_factory(sink)
    work(logger)
    logger.close()
    return [dict(r) for r in sink.records]


def plain(sink):
    return Logger("app", transports=[sink])


@scenario
def levels_and_filtering():
    def work(log):
        log.set_level("WARN")
        log.trace("t")
        log.debug("d")
        log.info("i")
        log.warn("w", a=1)
        log.error("e")
        log.fatal("f")

    return records(plain, work)


@scenario
def level_inputs():
    # Which inputs are accepted must agree. The wording of the refusals was settled differently
    # (decision D35) and is checked in tests/test_levels.py.
    out = {}
    for label, value in [("name", "debug"), ("weight", 30), ("upper", "ERROR"), ("mixed", "wArN")]:
        out[label] = str(Logger("x", level=value).level)
    for label, value in [("bad name", "loud"), ("bad weight", 25), ("float", 1.5), ("none", None)]:
        try:
            Logger("x", level=value)
            out[label] = "accepted"
        except (TypeError, ValueError):
            out[label] = "refused"
    return out


@scenario
def convenience_methods():
    def work(log):
        log.thought("t")
        log.action("a")
        log.observation("o")
        log.decision("d")
        log.thought("own kind", kind="custom")
        log.llm_call(
            model="m",
            tokens_in=3,
            tokens_out=4,
            cost_usd=0.5,
            latency_ms=12.5,
            finish_reason="stop",
            provider="p",
        )
        log.llm_call("named", tokens_in=-1, model=5)

    return records(plain, work)


@scenario
def record_shape_and_key_order():
    out = []

    def work(log):
        out.append(list(log.info("m", z=1, a=2)))

    records(plain, work)
    return out


@scenario
def context_and_priority():
    out = []

    def work(log):
        with bind_context(a=1, b="outer"):
            with bind_context(b="inner", c=3):
                log.info("nested", c="call")
                out.append(current_context())
            log.info("outer")
        out.append(current_context())

    recs = records(plain, work)
    return {"records": recs, "contexts": out}


@scenario
def child_loggers():
    def work(log):
        child = log.child("db", tier="storage")
        grand = child.child("pool")
        child.info("c")
        grand.info("g", own=1)
        log.set_level("ERROR")
        late = log.child("late")
        late.info("below")
        late.error("above")

    return records(plain, work)


@scenario
def spans_and_nesting():
    def work(log):
        with log.span("outer", attempt=1):
            log.info("inside outer")
            with log.span("inner"):
                log.info("inside inner")
        try:
            with log.span("fails"):
                raise ValueError("boom")
        except ValueError:
            pass
        with log.span("explicit", span_id="abc", parent_span_id="p1", kind="custom"):
            pass

    return records(plain, work)


@scenario
def state_diff():
    def work(log):
        state = {"n": 1, "keep": "x"}
        with log.span("s", capture_state=lambda: state):
            state["n"] = 2
            state["new"] = True
        with log.span("unchanged", capture_state=lambda: {"a": 1}):
            pass
        with log.span("raises", capture_state=lambda: 1 / 0):
            pass

    return records(plain, work)


@scenario
def retry_tracking():
    def work(log):
        log.action("call", tool="search", tool_call_id="1")
        log.action("again", tool="search", tool_call_id="1")
        log.action("third", tool="search", tool_call_id="1")
        log.observation("done", tool="search", tool_call_id="1")
        log.action("fresh", tool="search", tool_call_id="1")
        log.action("other tool", tool="calc")
        log.action("no tool")

    return records(plain, work)


@scenario
def plugins_order_and_drop():
    order = []

    def first(record):
        order.append("first")
        record["meta"]["first"] = True
        return record

    def drop_secret(record):
        order.append("drop")
        return None if record["message"] == "secret" else record

    def broken(record):
        order.append("broken")
        raise RuntimeError("plugin failure")

    def work(log):
        log.use(first).use(drop_secret).use(broken)
        log.info("kept")
        log.info("secret")

    return {"records": records(plain, work), "order": order}


@scenario
def lazy_values_and_caller():
    def work(log):
        log.opt(lazy=True).info("lazy", x=lambda: 42, boom=lambda: 1 / 0, plain=7)
        log.opt(depth=0).info("caller")

    recs = records(plain, work)
    for r in recs:
        caller = r["meta"].get("caller")
        if caller:
            caller["file"] = "<file>"
            caller["line"] = 0
    return recs


@scenario
def exceptions_and_exc_info():
    def work(log):
        try:
            raise KeyError("missing")
        except KeyError as error:
            log.error("with instance", exc_info=error)
            log.error("with true", exc_info=True)
        log.error("none handled", exc_info=True)
        log.error("bad exc_info", exc_info="nope")

    recs = records(plain, work)
    for r in recs:
        stack = r["meta"].get("stack")
        if stack:
            r["meta"]["stack"] = [
                line for line in stack.splitlines() if not line.lstrip().startswith("File ")
            ]
    return recs


@scenario
def toggle_rules():
    disable("noisy")
    enable("noisy.ok")

    def work(log):
        Logger("noisy", transports=log.transports).info("off")
        Logger("noisy.deep", transports=log.transports).info("off")
        Logger("noisy.ok", transports=log.transports).info("on")
        Logger("quiet", transports=log.transports).info("on")

    try:
        return records(plain, work)
    finally:
        from logquill.runtime import toggle

        toggle._reset()


@scenario
def content_policy():
    out = {}
    for policy in ("off", "hash", "truncate", "full"):

        def work(log):
            log.info(
                "m",
                input_messages=[{"role": "user", "content": "x" * 300}],
                tool_arguments={"q": "secret"},
                other="kept",
            )

        out[policy] = records(
            lambda sink, p=policy: Logger("app", transports=[sink], content_policy=p), work
        )
    return out


@scenario
def run_plugin_ids():
    def work(log):
        log.use(RunPlugin(run_id="fixed"))
        log.info("a")
        log.info("b")
        with log.span("s"):
            log.info("c")

    return records(plain, work)


@scenario
def thread_safety():
    sink = CollectingTransport()
    log = Logger("app", transports=[sink], async_dispatch=True)

    def spam(n):
        for i in range(200):
            log.info("m", t=n, i=i)

    threads = [threading.Thread(target=spam, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    log.close()
    per_thread = {}
    for r in sink.records:
        per_thread.setdefault(r["meta"]["t"], []).append(r["meta"]["i"])
    return {"count": len(sink.records), "ordered": all(v == sorted(v) for v in per_thread.values())}


@scenario
def async_dispatch_flush_and_close():
    sink = CollectingTransport()
    log = Logger("app", transports=[sink], async_dispatch=True)
    for i in range(100):
        log.info("m", i=i)
    drained = log.flush(5)
    after_flush = len(sink.records)
    log.close()
    return {"drained": drained, "after_flush": after_flush, "after_close": len(sink.records)}


def unescape_unicode(text):
    """`\\uXXXX` escapes as the characters they stand for (decision D32: new output is UTF-8)."""
    return re.sub(r"\\u([0-9a-f]{4})", lambda m: chr(int(m.group(1), 16)), text)


SAMPLE_RECORDS = [
    {"schema_version": "2.0", "timestamp": "2026-01-01T00:00:00.000Z", "level": "INFO", "logger": "app.api",
     "message": "user signed up", "meta": {"user_id": 42, "plan": "pro"}},
    {"schema_version": "2.0", "timestamp": "2026-01-01T00:00:00.000Z", "level": "ERROR", "logger": "app",
     "message": "caf\u00e9 \u2603 \U0001d11e", "meta": {"stack": "Traceback\n  File x\nValueError: boom",
                                                    "nested": {"a": {"b": [1, 2, {"c": None}]}}, "t": True, "f": 1.5}},
    {"schema_version": "2.0", "timestamp": "2026-01-01T00:00:00.000Z", "level": "INFO", "logger": "app",
     "message": "llm", "meta": {"kind": "action"}, "llm": {"model": "m", "tokens_in": 3, "cost_usd": 0.25}},
    {"schema_version": "2.0", "timestamp": "2026-01-01T00:00:00.000Z", "level": "WARN", "logger": "app",
     "message": "needs quotes", "meta": {"a b": "x y", "eq": "k=v", "quote": 'say "hi"', "empty": "",
                                         "level": "meta-level", "timestamp": "meta-ts", "tab": "a\tb"}},
    {"schema_version": "2.0", "timestamp": "2026-01-01T00:00:00.000Z", "level": "INFO", "logger": "app",
     "message": "", "meta": {}},
]


@scenario
def formatters_agree():
    from logquill import JSONFormatter, LogfmtFormatter, TextFormatter

    out = []
    for record in SAMPLE_RECORDS:
        out.append(
            {
                "json": json.loads(JSONFormatter().format(record)),
                "text": unescape_unicode(TextFormatter().format(record)),
                "logfmt": unescape_unicode(LogfmtFormatter().format(record)),
            }
        )
    partial = {"message": "only a message"}
    out.append({"partial_text": TextFormatter().format(partial)})
    return out


@scenario
def logfmt_round_trip_and_parsing():
    from logquill import LogfmtFormatter, parse_logfmt

    lines = [
        'a=1 b="two words" c=',
        'bare key=v',
        'q="esc \\" and \\\\ and \\n and \\u00e9" last=1',
        "dup=1 dup=2",
        "",
        "=novalue x=y",
    ]
    out = {"lines": [parse_logfmt(line) for line in lines]}
    out["round_trip"] = [parse_logfmt(LogfmtFormatter().format(r)) for r in SAMPLE_RECORDS]
    return out


@scenario
def parse_record_cases():
    from logquill import parse_record

    cases = {
        "valid": SAMPLE_RECORDS[0],
        "legacy": {"timestamp": "t", "level": "INFO", "logger": "l", "message": "m", "extra": 1},
        "no meta": {"schema_version": "2.0", "timestamp": "t", "level": "INFO", "logger": "l", "message": "m"},
        "newer minor": {"schema_version": "2.7", "timestamp": "t", "level": "INFO", "logger": "l", "message": "m", "meta": {}, "new": 1},
        "newer major": {"schema_version": "3.0", "timestamp": "t", "level": "INFO", "logger": "l", "message": "m"},
        "bad version": {"schema_version": "two", "timestamp": "t", "level": "INFO", "logger": "l", "message": "m"},
        "version type": {"schema_version": 2, "timestamp": "t", "level": "INFO", "logger": "l", "message": "m"},
        "missing field": {"schema_version": "2.0", "level": "INFO", "logger": "l", "message": "m"},
        "bad level": {"schema_version": "2.0", "timestamp": "t", "level": "info", "logger": "l", "message": "m"},
        "meta type": {"schema_version": "2.0", "timestamp": "t", "level": "INFO", "logger": "l", "message": "m", "meta": []},
        "llm type": {"schema_version": "2.0", "timestamp": "t", "level": "INFO", "logger": "l", "message": "m", "llm": 5},
        "not an object": ["a"],
    }
    out = {}
    for name, raw in cases.items():
        before = json.dumps(raw, sort_keys=True)
        try:
            out[name] = parse_record(raw)
        except Exception as error:
            # The core names JSON types ("number", "array") and Python named its own ("int",
            # "list"); the rest of the message must agree.
            text = f"{type(error).__name__}: {error}".replace('"', "'")  # JSON vs Python quoting
            out[name] = re.sub(r" got .*", " got <value>", text)
        assert json.dumps(raw, sort_keys=True) == before, "input was modified"
    return out


@scenario
def timestamps_have_the_documented_shape():
    from logquill.core.records import utc_timestamp

    stamp = utc_timestamp()
    return bool(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", stamp))


def run_all(names=None):
    results = {}
    for name, fn in SCENARIOS.items():
        if names and name not in names:
            continue
        try:
            results[name] = mask(fn())
        except Exception as error:
            results[name] = {"scenario raised": f"{type(error).__name__}: {error}"}
    return results
