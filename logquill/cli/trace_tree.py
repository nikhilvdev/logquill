"""Reconstructing one agent run's span tree from its own records, and
rendering it as an indented, annotated tree — the shared engine behind
`logquill trace` and `logquill serve`.

Streams: `TraceBuilder.feed()` takes one record at a time and only holds
onto records that belong to the run being built, so tracing one run out of
a huge log file costs memory proportional to that run, not the file (see
`logquill/cli.py`'s `trace` command, which reads a file line by line).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Rollup:
    """Token/cost totals — one node's own, or its subtree's."""

    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: Rollup) -> Rollup:
        return Rollup(
            self.tokens_in + other.tokens_in,
            self.tokens_out + other.tokens_out,
            self.cost_usd + other.cost_usd,
        )


@dataclass
class TraceNode:
    """One record in a run's trace, plus everything nested directly inside
    it — a mix of further spans and plain events (actions, observations,
    thoughts, decisions, or an ordinary log line), in the order they were
    written. That mix, not a spans-only tree, is what a waterfall view needs:
    the true nesting order of everything that happened inside a span.
    """

    record: Mapping[str, Any]
    children: list[TraceNode] = field(default_factory=list)

    @property
    def _meta(self) -> Mapping[str, Any]:
        meta = self.record.get("meta")
        return meta if isinstance(meta, dict) else {}

    @property
    def span_id(self) -> str | None:
        """This node's own span id — set only on a record `Logger.span()`
        wrote on exit, `None` for a plain event."""
        span_id = self._meta.get("span_id")
        return span_id if isinstance(span_id, str) and span_id else None

    @property
    def parent_span_id(self) -> str | None:
        parent = self._meta.get("parent_span_id")
        return parent if isinstance(parent, str) and parent else None

    @property
    def is_span(self) -> bool:
        return self._meta.get("kind") == "span"

    @property
    def duration_ms(self) -> float | None:
        value = self._meta.get("duration_ms")
        return (
            float(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else None
        )

    @property
    def own_tokens_cost(self) -> Rollup:
        llm = self.record.get("llm")
        llm = llm if isinstance(llm, dict) else {}
        tokens_in = llm.get("tokens_in")
        tokens_out = llm.get("tokens_out")
        cost = llm.get("cost_usd")
        return Rollup(
            tokens_in if isinstance(tokens_in, int) and not isinstance(tokens_in, bool) else 0,
            tokens_out if isinstance(tokens_out, int) and not isinstance(tokens_out, bool) else 0,
            float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else 0.0,
        )

    def rollup(self) -> Rollup:
        """This node's own tokens/cost plus everything nested under it."""
        total = self.own_tokens_cost
        for child in self.children:
            total = total + child.rollup()
        return total

    def to_dict(self) -> dict[str, Any]:
        """A plain, JSON-safe nested structure — what `logquill trace
        --json` prints and what `logquill serve`'s `/api/trace` returns."""
        rollup = self.rollup()
        return {
            "record": dict(self.record),
            "rollup": {
                "tokens_in": rollup.tokens_in,
                "tokens_out": rollup.tokens_out,
                "cost_usd": rollup.cost_usd,
            },
            "children": [child.to_dict() for child in self.children],
        }


class TraceBuilder:
    """Single-pass, streaming reconstruction of one `run_id`'s span tree.

    Feed records in file order via `feed()`; call `finalize()` once, after
    the last one, to get the roots. Relies on how `Logger.span()` writes
    records: everything logged inside a span appears in the file *before*
    that span's own closing record, and a nested span closes before its
    parent (ordinary `with`-block exit order) — so by the time a span's own
    record is seen, everything nested inside it has already been seen too,
    and this never needs to look ahead or buffer more than one run's worth
    of records.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._nodes: dict[str, TraceNode] = {}
        #: Records/spans waiting for a parent span that hasn't closed yet,
        #: keyed by the parent's `span_id`.
        self._pending: dict[str, list[TraceNode]] = {}
        self.roots: list[TraceNode] = []
        self.record_count = 0

    def feed(self, record: Mapping[str, Any]) -> bool:
        """Considers one record. Returns whether it belongs to this run (and
        was kept) — records for any other run cost nothing beyond this call."""
        meta = record.get("meta")
        meta = meta if isinstance(meta, dict) else {}
        if meta.get("run_id") != self.run_id:
            return False

        self.record_count += 1
        node = TraceNode(record)
        if node.span_id is not None:
            node.children = self._pending.pop(node.span_id, [])
            self._nodes[node.span_id] = node

        parent_span_id = node.parent_span_id
        if parent_span_id is None:
            self.roots.append(node)
        else:
            parent = self._nodes.get(parent_span_id)
            if parent is not None:
                parent.children.append(node)
            else:
                self._pending.setdefault(parent_span_id, []).append(node)
        return True

    def finalize(self) -> list[TraceNode]:
        """Call once every record has been fed. Any node still waiting for a
        parent that never closed (a still-open span in a crashed or
        truncated run) is appended to the roots instead of being silently
        dropped. Returns `self.roots`."""
        for orphans in self._pending.values():
            self.roots.extend(orphans)
        self._pending.clear()
        return self.roots


def build_trace(records: Iterable[Mapping[str, Any]], run_id: str) -> TraceBuilder:
    """Convenience wrapper: feed every record in `records` and finalize."""
    builder = TraceBuilder(run_id)
    for record in records:
        builder.feed(record)
    builder.finalize()
    return builder


def _annotations(node: TraceNode) -> str:
    parts = []
    if node.duration_ms is not None:
        parts.append(f"{node.duration_ms:,.1f}ms")
    rollup = node.rollup()
    if rollup.tokens_in or rollup.tokens_out:
        parts.append(f"{rollup.tokens_in}→{rollup.tokens_out} tok")
    if rollup.cost_usd:
        parts.append(f"${rollup.cost_usd:,.4f}")
    return f"  ({', '.join(parts)})" if parts else ""


def _label(node: TraceNode) -> str:
    level = str(node.record.get("level", "?"))
    message = str(node.record.get("message", ""))
    return f"[{level}] {message}{_annotations(node)}"


def render_tree(
    roots: list[TraceNode],
    *,
    colorize: Callable[[str, str], str] | None = None,
) -> str:
    """Renders `roots` (from `TraceBuilder.finalize()`/`build_trace()`) as an
    indented tree, one line per record, each annotated with its own
    `duration_ms` and the token/cost totals of everything nested under it
    (so an outer span shows the sum its children contributed).

    `colorize(text, level) -> text`, if given, wraps each line's label —
    `logquill trace` passes the same level-to-color mapping `ConsoleTransport`
    uses; left out (the default) for output going to a file or a non-TTY.
    """
    lines: list[str] = []

    def walk(node: TraceNode, prefix: str, is_last: bool) -> None:
        connector = "└─ " if is_last else "├─ "
        label = _label(node)
        if colorize is not None:
            label = colorize(label, str(node.record.get("level", "")))
        lines.append(f"{prefix}{connector}{label}")
        child_prefix = prefix + ("   " if is_last else "│  ")
        for index, child in enumerate(node.children):
            walk(child, child_prefix, index == len(node.children) - 1)

    for index, root in enumerate(roots):
        walk(root, "", index == len(roots) - 1)
    return "\n".join(lines)
