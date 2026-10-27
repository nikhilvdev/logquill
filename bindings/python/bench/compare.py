"""Runs bench.py under the 2.0.0 interpreter and this one, prints a table, and exits non-zero if
this version is slower than 2.0.0 on a caller-side latency row by more than the allowed margin.

    python compare.py OLD_PYTHON [NEW_PYTHON] [--margin 1.10]
"""

import json
import os
import statistics
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
margin = float(sys.argv[sys.argv.index("--margin") + 1]) if "--margin" in sys.argv else 1.10
if "--margin" in sys.argv:
    args.remove(sys.argv[sys.argv.index("--margin") + 1])
old_python, new_python = args[0], (args[1] if len(args) > 1 else sys.executable)


def run(python):
    done = subprocess.run([python, os.path.join(here, "bench.py")], capture_output=True, text=True, timeout=900)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout)


# The queued row times a hand-off to a background thread, which swings on a shared CI runner; one run per
# interpreter failed a build at 1.12x and 1.48x with identical code. Take each row's median of three runs,
# alternating old and new so a slow stretch of the machine lands on both.
RUNS = 3
olds, news = [], []
for _ in range(RUNS):
    olds.append(run(old_python))
    news.append(run(new_python))
old = {"results": {k: statistics.median(r["results"][k] for r in olds) for k in olds[0]["results"]}}
new = {"results": {k: statistics.median(r["results"][k] for r in news) for k in news[0]["results"]}}
print(f"{'':62} {'2.0.0':>10} {'3.0':>10} {'ratio':>8}")
failed = []
for name in old["results"]:
    o, n = old["results"][name], new["results"][name]
    ratio = n / o if o else float("nan")
    unit = "" if "KiB" in name else " ns"
    print(f"{name:62} {o:>10}{unit} {n:>8}{unit} {ratio:>7.2f}x")
    if "KiB" not in name and ratio > margin:
        failed.append(name)
if failed:
    print("\nSlower than 2.0.0 beyond the margin:", ", ".join(failed))
    sys.exit(1)
