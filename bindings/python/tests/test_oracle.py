"""Differential tests: the same scenarios under logquill 2.0.0 and under this version must agree.

Needs an interpreter with the published 2.0.0 installed:

    uv venv /tmp/logquill-old && uv pip install --python /tmp/logquill-old/bin/python logquill==2.0.0
    LOGQUILL_ORACLE_PYTHON=/tmp/logquill-old/bin/python pytest tests/test_oracle.py

Without the variable these tests are skipped.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ORACLE = Path(__file__).parent / "oracle"
OLD_PYTHON = os.environ.get("LOGQUILL_ORACLE_PYTHON")

pytestmark = pytest.mark.skipif(not OLD_PYTHON, reason="LOGQUILL_ORACLE_PYTHON is not set")

sys.path.insert(0, str(ORACLE))
import scenarios  # noqa: E402


def run(python):
    done = subprocess.run(
        [python, "run_all.py"], cwd=ORACLE, capture_output=True, text=True, timeout=300
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout)


@pytest.fixture(scope="session")
def results():
    return run(OLD_PYTHON), run(sys.executable)


@pytest.mark.parametrize("name", sorted(scenarios.SCENARIOS))
def test_scenario_agrees_with_2_0_0(name, results):
    old, new = results
    assert new[name] == old[name]


# ---- chains cross the version boundary in both directions ------------------------------------

WRITE_CHAIN = """
import sys
from logquill import FileTransport, Logger, TamperEvidentPlugin
log = Logger("app", transports=[FileTransport(sys.argv[1])], plugins=[TamperEvidentPlugin()])
for i in range(25):
    log.info("record", i=i, text="caf\\u00e9 \\u2603 \\U0001d11e", ratio=0.1 * i, big=2**70, flag=i % 2 == 0)
log.close()
"""

VERIFY_CHAIN = """
import json, sys
from logquill import verify_chain_detailed
records = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
result = verify_chain_detailed(records)
print(json.dumps([result.ok, result.records_checked, result.reason]))
"""


def python_run(python, code, path):
    done = subprocess.run([python, "-c", code, str(path)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    return done.stdout


def test_a_chain_written_by_3_0_verifies_under_2_0_0(tmp_path):
    path = tmp_path / "new.log"
    python_run(sys.executable, WRITE_CHAIN, path)
    assert json.loads(python_run(OLD_PYTHON, VERIFY_CHAIN, path)) == [True, 25, None]


def test_a_chain_written_by_2_0_0_verifies_under_3_0(tmp_path):
    path = tmp_path / "old.log"
    python_run(OLD_PYTHON, WRITE_CHAIN, path)
    assert json.loads(python_run(sys.executable, VERIFY_CHAIN, path)) == [True, 25, None]


def test_both_versions_reject_the_same_tampering(tmp_path):
    path = tmp_path / "new.log"
    python_run(sys.executable, WRITE_CHAIN, path)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:5] + [lines[5].replace('"i":5', '"i":55')] + lines[6:]) + "\n", encoding="utf-8")
    new = json.loads(python_run(sys.executable, VERIFY_CHAIN, path))
    old = json.loads(python_run(OLD_PYTHON, VERIFY_CHAIN, path))
    assert new == old and new[0] is False and new[1] == 6
