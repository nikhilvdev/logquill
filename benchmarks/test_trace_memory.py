"""The exit criterion for the local-first trace viewer: reconstructing one
run's span tree out of a multi-gigabyte log file costs memory proportional
to that run, not the file. Slow and memory-instrumented on purpose, so it
lives here rather than in the default `pytest` run — see `measure.py`'s
module docstring for why these are a separate CI job.
"""

from __future__ import annotations

import json
import tracemalloc
from pathlib import Path
from typing import Any, Iterator

from logquill.cli.trace_tree import build_trace

#: Comfortably over 1 GB — the scale this test is meant to prove, without
#: depending on exactly hitting it.
TARGET_BYTES = 1_100_000_000

#: However large the file gets, reconstructing one small run out of it must
#: stay nowhere near proportional to the file — a few MB, not gigabytes.
MEMORY_BUDGET_BYTES = 100_000_000

NEEDLE_RUN_ID = "needle-run"

_NOISE_META = {"user_id": 42, "route": "/checkout", "tags": ["a", "b", "c"], "note": "x" * 40}


def _record(*, run_id: str, message: str, **meta: Any) -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "timestamp": "2026-01-01T00:00:00.000Z",
        "level": "INFO",
        "logger": "app.agent",
        "message": message,
        "meta": {"run_id": run_id, **meta},
    }


def _needle_run_records() -> list[dict[str, Any]]:
    """A small, ordinary agent run — this is what the test must be able to
    reconstruct out of the noise around it."""
    span_id, step_id = "a" * 16, "b" * 16
    return [
        _record(
            run_id=NEEDLE_RUN_ID,
            message="thought",
            kind="thought",
            parent_span_id=span_id,
        ),
        {
            **_record(run_id=NEEDLE_RUN_ID, message="chat", kind="action", parent_span_id=step_id),
            "llm": {"model": "m", "tokens_in": 10, "tokens_out": 5, "cost_usd": 0.01},
        },
        _record(
            run_id=NEEDLE_RUN_ID,
            message="step",
            kind="span",
            span_id=step_id,
            parent_span_id=span_id,
            duration_ms=5.0,
        ),
        _record(
            run_id=NEEDLE_RUN_ID,
            message="run",
            kind="span",
            span_id=span_id,
            operation="invoke_agent",
            agent_name="planner",
            duration_ms=12.0,
        ),
    ]


def _write_large_jsonl(path: Path, *, target_bytes: int) -> int:
    """Writes `target_bytes`+ of noise from many unrelated runs, with the
    needle run's records inserted partway through — returns how many needle
    records were written."""
    needle = [json.dumps(r, separators=(",", ":")) for r in _needle_run_records()]
    noise_run_ids = [f"noise-{i}" for i in range(1000)]
    written = 0
    needle_written = False
    with path.open("w", encoding="utf-8") as f:
        i = 0
        while written < target_bytes:
            if not needle_written and written > target_bytes // 2:
                for line in needle:
                    f.write(line + "\n")
                    written += len(line) + 1
                needle_written = True
            line = json.dumps(
                _record(
                    run_id=noise_run_ids[i % len(noise_run_ids)], message="noise", **_NOISE_META
                ),
                separators=(",", ":"),
            )
            f.write(line + "\n")
            written += len(line) + 1
            i += 1
    return len(needle)


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


def test_a_gigabyte_scale_log_file_is_traced_with_bounded_memory(tmp_path: Path) -> None:
    path = tmp_path / "huge.jsonl"
    needle_count = _write_large_jsonl(path, target_bytes=TARGET_BYTES)
    file_size = path.stat().st_size
    assert file_size >= TARGET_BYTES  # the scale this test is actually about

    tracemalloc.start()
    try:
        baseline, _ = tracemalloc.get_traced_memory()
        builder = build_trace(_read_jsonl(path), NEEDLE_RUN_ID)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert builder.record_count == needle_count
    (root,) = builder.roots
    assert root.record["message"] == "run"
    assert root.rollup().tokens_in == 10

    peak_bytes = peak - baseline
    assert peak_bytes < MEMORY_BUDGET_BYTES, (
        f"reconstructing one run used {peak_bytes:,} bytes against a "
        f"{file_size:,}-byte file — memory grew with the file, not the run"
    )
