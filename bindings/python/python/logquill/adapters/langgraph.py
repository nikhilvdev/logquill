from __future__ import annotations

from typing import Any

try:
    from langgraph.callbacks import GraphCallbackHandler  # type: ignore[import-not-found]
except ImportError as exc:
    raise ImportError(
        "logquill.adapters.langgraph requires the optional `langgraph` "
        "dependency — install with `pip install logquill[langgraph]`."
    ) from exc

from logquill.adapters.langchain import LangChainAdapter


def _checkpoint_meta(event: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {"checkpoint_id": event.checkpoint_id, "status": event.status}
    if event.checkpoint_ns:
        meta["checkpoint_ns"] = list(event.checkpoint_ns)
    if event.run_id is not None:
        meta["parent_span_id"] = str(event.run_id)
    return meta


# `type: ignore[misc]` — same reason as every other adapter here:
# `GraphCallbackHandler` types as `Any` whenever `langgraph` isn't installed
# (optional, never in `dev` — see pyproject.toml), and mypy refuses to let a
# class subclass something typed `Any`.
class LangGraphAdapter(LangChainAdapter, GraphCallbackHandler):  # type: ignore[misc]
    """`LangChainAdapter` plus LangGraph's own checkpoint pause/resume events.

    `LangChainAdapter` alone already covers everything that happens *inside*
    a LangGraph node — nodes execute as ordinary LangChain `Runnable`s, so
    `on_chain_start`/`on_llm_start`/`on_tool_start`/etc. all fire exactly as
    they would for a plain chain. What a plain `BaseCallbackHandler` cannot
    see is LangGraph's own checkpoint lifecycle: `on_interrupt`/`on_resume`,
    fired when a graph pauses on an `interrupt()` call (e.g. for human
    review) and later resumes from a persisted checkpoint. LangGraph
    dispatches those two specifically to handlers that are instances of its
    own `GraphCallbackHandler` — a plain `BaseCallbackHandler` subclass
    (which is all `LangChainAdapter` is) never receives them, silently. This
    class exists for that reason alone; everything else is inherited
    unchanged from `LangChainAdapter`.

        from logquill import Logger, RunPlugin
        from logquill.adapters.langgraph import LangGraphAdapter

        log = Logger("app")
        handler = LangGraphAdapter(log.child("agent").use(RunPlugin()))
        graph = builder.compile(checkpointer=checkpointer)
        graph.invoke(input, config={"callbacks": [handler], "configurable": {"thread_id": "1"}})

    `on_interrupt` becomes `.observation("graph_interrupted", ...)` carrying
    `checkpoint_id`, `status`, `checkpoint_ns` (the subgraph namespace path,
    if nested), and each pending `Interrupt`'s `id`/`value`; `on_resume`
    becomes `.action("graph_resumed", ...)` with the same checkpoint fields.
    Both use `event.run_id` as `parent_span_id`, matching the enclosing
    graph's own chain span from `LangChainAdapter.on_chain_start` — the
    graph hasn't ended, just paused, so its span is still open.

    `pip install logquill[langgraph]` — pulls in a compatible `langchain-core`
    transitively, so installing this extra alone is enough; `langgraph` is
    never imported unless you import `logquill.adapters.langgraph` yourself.
    """

    def on_interrupt(self, event: Any) -> None:
        """Emits `.observation("graph_interrupted")` when the graph pauses on
        an `interrupt()` call, carrying each pending interrupt's `id`/`value`
        plus the checkpoint fields."""
        meta = _checkpoint_meta(event)
        meta["interrupts"] = [{"id": i.id, "value": i.value} for i in event.interrupts]
        self.log.observation("graph_interrupted", **meta)

    def on_resume(self, event: Any) -> None:
        """Emits `.action("graph_resumed")` when the graph resumes from a
        persisted checkpoint."""
        self.log.action("graph_resumed", **_checkpoint_meta(event))
