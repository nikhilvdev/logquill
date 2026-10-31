#!/usr/bin/env python3
"""Runs the old Python `logquill` CLI and the Rust one on the same fixture files and compares them.

    python3 scripts/compare_old_cli.py OLD_LOGQUILL NEW_LOGQUILL

OLD_LOGQUILL is the `logquill` command from `pip install logquill==2.0.0` (its package must be
importable by the interpreter next to it); NEW_LOGQUILL is the Rust binary. Output must match on
stdout, stderr and exit code, apart from the differences the project settled on (see the list in
`docs/stages/stage-4.md`): non-ASCII is raw UTF-8 not `\\uXXXX` (D32), JSON is compared as parsed
values, and `trace` warnings say `trace`, not the old copy-paste `tail`.
"""
import json, os, re, subprocess, sys, tempfile

old_cli, new_cli = (os.path.abspath(p) for p in sys.argv[1:3])
old_python = os.path.join(os.path.dirname(old_cli), "python")


def rec(level, msg, meta, llm=None):
    d = {"schema_version": "2.0", "timestamp": "2026-01-01T00:00:00.000Z", "level": level,
         "logger": "app", "message": msg, "meta": meta}
    if llm:
        d["llm"] = llm
    return json.dumps(d, ensure_ascii=False)


lines = [
    rec("DEBUG", "warmup", {"x": 1}),
    rec("INFO", "call_llm", {"run_id": "r1", "span_id": "s2", "parent_span_id": "s1", "duration_ms": 1200.5},
        {"model": "m", "tokens_in": 1200, "tokens_out": 340, "cost_usd": 0.01234}),
    rec("INFO", "tool search", {"run_id": "r1", "span_id": "s3", "parent_span_id": "s1", "duration_ms": 12345.678}),
    "not json at all",
    "[1, 2, 3]",
    rec("WARN", "slow thing café ☃", {"run_id": "r1", "span_id": "s4", "parent_span_id": "s1", "who": "zoë"}),
    rec("INFO", "agent", {"run_id": "r1", "span_id": "s1", "kind": "span", "duration_ms": 2500000.25}),
    rec("ERROR", "failed", {"run_id": "r2", "stack": "Traceback (most recent call last):\n  File \"a.py\", line 1\nValueError: boom"}),
    rec("INFO", "orphan child", {"run_id": "r2", "span_id": "c1", "parent_span_id": "never-closed"}),
    rec("FATAL", "dead", {"run_id": "r2"}),
    rec("TRACE", "t", {}),
    json.dumps({"message": "no level", "meta": {}}),
]
work = tempfile.mkdtemp()
fixture = os.path.join(work, "fixture.log")
open(fixture, "w").write("\n".join(lines) + "\n")

# A hash-chained log written by the old library, then damaged three ways.
chained = os.path.join(work, "chained.log")
subprocess.run([old_python, "-c", f"""
from logquill import Logger, FileTransport
from logquill.plugins.tamper_evident_plugin import TamperEvidentPlugin
log = Logger("app", transports=[FileTransport({chained!r})]); log.use(TamperEvidentPlugin())
for i in range(5): log.info(f"m{{i}}", n=i, name="café")
log.close()"""], check=True)
chain = open(chained).read().splitlines()
variants = {
    "edited": chain[:2] + [chain[2].replace("m2", "mX")] + chain[3:],
    "removed": chain[:2] + chain[3:],
    "truncated": chain[:3],
}
for name, rows in variants.items():
    open(os.path.join(work, name + ".log"), "w").write("\n".join(rows) + "\n")


def run(cli, args):
    p = subprocess.run([cli] + args, capture_output=True, text=True, cwd=work)
    return p.stdout, p.stderr, p.returncode


def normal(text):
    text = re.sub(r"\\u([0-9a-f]{4})", lambda m: chr(int(m[1], 16)), text)  # D32
    out = []
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            out.append(line)
    return out


cases = [
    ("tail", ["tail", "fixture.log", "--no-color"]),
    ("tail --level WARN", ["tail", "fixture.log", "--level", "WARN", "--no-color"]),
    ("tail --json", ["tail", "fixture.log", "--json"]),
    ("tail -n 3", ["tail", "fixture.log", "-n", "3", "--no-color"]),
    ("tail --level ERROR -n 1 --json", ["tail", "fixture.log", "--level", "ERROR", "-n", "1", "--json"]),
    ("tail unicode meta", ["tail", "chained.log", "--no-color"]),
    ("tail missing file", ["tail", "nofile.log"]),
    ("trace r1", ["trace", "r1", "--file", "fixture.log", "--no-color"]),
    ("trace r2 (orphan span)", ["trace", "r2", "--file", "fixture.log", "--no-color"]),
    ("trace r1 --json", ["trace", "r1", "--file", "fixture.log", "--json"]),
    ("trace unknown run", ["trace", "nope", "--file", "fixture.log"]),
    ("trace missing file", ["trace", "r1", "--file", "nofile.log"]),
    ("verify intact", ["verify", "chained.log"]),
    ("verify edited line", ["verify", "edited.log"]),
    ("verify removed line", ["verify", "removed.log"]),
    ("verify truncated, unsigned", ["verify", "truncated.log"]),
    ("verify one flag only", ["verify", "chained.log", "--sign-key", "00"]),
    ("verify bad hex", ["verify", "chained.log", "--sign-key", "zz", "--signature", "00"]),
    ("verify no chain", ["verify", "fixture.log"]),
]
failed = 0
for name, args in cases:
    (oo, oe, oc), (no, ne, nc) = run(old_cli, args), run(new_cli, args)
    oe = oe.replace("logquill tail: skipping", "logquill trace: skipping") if args[0] == "trace" else oe
    same = normal(oo) == normal(no) and normal(oe) == normal(ne) and oc == nc
    print(("SAME  " if same else "DIFF  ") + f"{name} (exit {oc})")
    if not same:
        failed += 1
        print("  old:", oo[:300], oe[:300], "\n  new:", no[:300], ne[:300], f"\n  exit old={oc} new={nc}")
sys.exit(1 if failed else 0)
