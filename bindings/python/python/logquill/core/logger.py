from __future__ import annotations

import contextlib
import logging
import threading
from typing import Any, Callable

from logquill.core.context import _current_context
from logquill.core.exceptions import LocalRedactor, format_exc_info
from logquill.core.levels import Level, parse_level
from logquill.core.opt import OptLogger, caller_info, resolve_lazy
from logquill.core.records import SCHEMA_VERSION, LLMBlock, LogRecord, build_llm_block
from logquill.plugins.context_plugin import ContextPlugin
from logquill.plugins.plugin import FunctionPlugin, MiddlewareFunc, Plugin
from logquill.privacy.privacy import (
    CONTENT_FIELDS,
    ContentCapturePolicy,
    apply_content_policy,
    parse_content_policy,
)
from logquill.runtime import shutdown
from logquill.runtime import toggle as _toggle
from logquill.runtime.toggle import is_enabled
from logquill import _native
from logquill.runtime.worker import BackpressurePolicy
from logquill.tracing.retry import RetryTracker
from logquill.tracing.span import SpanContext, _current_span_id, current_span_id
from logquill.transports.transport import Transport

_logger = logging.getLogger("logquill")

#: Looked up on every log call, so kept as module-level names rather than rebuilt each time.
_CONTENT_FIELDS = frozenset(CONTENT_FIELDS)
_LEVEL_NAMES = {level: level.name for level in Level}
_now = _native.utc_timestamp


def _warn(message: str) -> None:
    """Where the Rust dispatcher reports problems (a full queue, a transport that failed)."""
    _logger.warning("%s", message)


class Logger:
    """A named, leveled logger that runs records through a plugin pipeline
    before writing them to one or more transports.

    Construct directly, or via `.child()` to derive a namespaced logger that
    shares this one's transports. See `__init__` for what `async_dispatch`
    changes about ordering.
    """

    def __init__(
        self,
        name: str,
        level: int | str | Level = Level.INFO,
        transports: list[Transport] | None = None,
        plugins: list[Plugin | MiddlewareFunc] | None = None,
        async_dispatch: bool = True,
        max_queue_size: int = 10_000,
        backpressure: BackpressurePolicy = "drop_oldest",
        flush_at_exit: bool = True,
        content_policy: ContentCapturePolicy | str = "off",
        meta: dict[str, Any] | None = None,
    ) -> None:
        """`async_dispatch=True` (the default, decision D2) queues each finished record for a
        background thread in the Rust core, which formats it and writes it to the transports and then
        runs the plugins' `after_log` hooks, so `.info()`/`.error()`/... return without waiting on a
        transport's I/O. `before_log` plugin hooks still run on the caller's thread, since they can
        filter or transform the record and a later hook or transport needs the result in order.
        Pass `async_dispatch=False` to write on the caller's thread instead (tests, short scripts).

        `max_queue_size`/`backpressure` bound the queue: a stalled transport cannot grow memory
        without limit. `"drop_oldest"` (default) evicts the oldest queued record, `"drop_newest"`
        discards the new one, `"block"` makes the caller wait. Either drop policy warns at most once
        a minute. Both are ignored with `async_dispatch=False`.

        `flush_at_exit=True` (the default) drains queued records and closes
        this logger's transports when the interpreter exits, so a script that
        ends without calling `close()` doesn't lose its last records or a
        batching transport's unsent batch. Pass `False` if you manage
        shutdown yourself. It runs on a normal exit (end of script,
        `sys.exit()`, an unhandled exception) but not when the process is
        killed outright (`SIGKILL`, `os._exit()`, or `SIGTERM` with no
        handler installed) — handle `SIGTERM` and call `close()` for that.

        `content_policy` governs the content fields — an LLM call's prompt/
        completion, a tool call's arguments/result, a span's captured state
        (see `logquill.privacy.CONTENT_FIELDS`) — **`"off"` by default**:
        every call site, this logger's own plugins, and every transport
        never see the raw value unless you choose otherwise. `"hash"`
        replaces it with a stable digest (same content, same hash, so two
        records can be confirmed to share one without exposing it);
        `"truncate"` keeps the first `logquill.privacy.TRUNCATE_CHARS`
        characters; `"full"` passes it through. Applied before any plugin
        sees the record, so no plugin — not even a custom one — can
        override it; see `logquill.privacy` for the full explanation and
        `AuditLogger` for a profile that bundles this with the other
        privacy/integrity controls.

        `meta` is fixed context stamped on every record this logger and its children produce
        (`service`, `region`, ...). A key a call passes itself wins over it, and a child's own
        `child(**fixed_meta)` wins over it. A child's `fixed_meta` is not passed on to its own
        children (plugins are not inherited, decision D1); `meta=` is.
        """
        self.name = name
        self._base_meta: dict[str, Any] = dict(meta) if meta else {}
        self._level = parse_level(level)
        self.content_policy: ContentCapturePolicy = parse_content_policy(content_policy)
        self.transports: list[Transport] = list(transports) if transports else []
        self.plugins: list[Plugin] = []
        self._after_plugins: list[Plugin] = []
        #: The native hash chain, if a plugin (`TamperEvidentPlugin`) asked for one.
        self._chain: object | None = None
        if self._base_meta:
            self.use(ContextPlugin(**self._base_meta))
        for plugin in plugins or []:
            self.use(plugin)
        self._async = async_dispatch
        self._dispatcher: _native.Dispatcher | None = (
            _native.Dispatcher(
                [getattr(t, "_native_dispatch", None) or t for t in self.transports],
                max_queue_size,
                backpressure,
                _warn,
            )
            if self.transports
            else None
        )
        self._host_only = self._only_host_transports()
        # for the direct write to Python-only transports (see `_write_host`); shared with children
        self._write_lock = threading.Lock()
        self._open = [True]
        self._retries = RetryTracker()
        self._opt_cache: dict[tuple[bool, int | None], OptLogger] = {}
        if flush_at_exit:
            shutdown.register(self)

    def _only_host_transports(self) -> bool:
        """Whether every transport is Python code (none can be handed to the Rust dispatcher)."""
        return all(getattr(t, "_native_dispatch", None) is None for t in self.transports)

    def _share_with(self, parent: Logger) -> None:
        """Take over `parent`'s transports and dispatcher, so a child writes to the same sinks on
        the same queue instead of starting a second one."""
        self.transports = parent.transports
        self._dispatcher = parent._dispatcher
        self._async = parent._async
        self._host_only = parent._host_only
        self._write_lock = parent._write_lock
        self._open = parent._open

    @property
    def level(self) -> Level:
        """The minimum level this logger currently accepts."""
        return self._level

    def set_level(self, level: int | str | Level) -> None:
        """Change the minimum level this logger accepts; accepts an int, a
        level name, or a `Level` member."""
        self._level = parse_level(level)

    def use(self, plugin: Plugin | MiddlewareFunc) -> Logger:
        """Register a plugin, or a plain `before_log`-style function.

        A function is wrapped internally as an anonymous `Plugin`
        (`FunctionPlugin`) — the same middleware ergonomics as Express/Koa,
        without needing to read the `Plugin` base class first. Returns
        `self` so calls can be chained.
        """
        if not isinstance(plugin, Plugin):
            plugin = FunctionPlugin(plugin)
        self.plugins.append(plugin)
        if plugin._chain is not None:
            self._chain = plugin._chain
        if type(plugin).after_log is not Plugin.after_log:
            self._after_plugins.append(plugin)
        return self

    def child(self, name: str, /, **fixed_meta: Any) -> Logger:
        """A namespaced logger under this one: `f"{self.name}.{name}"`.

        Shares this logger's transports (the same sink instances, so
        `close()` on either flushes both) but starts with its own empty
        plugin list — plugins are per-logger middleware, not inherited, so
        a child can `.use(RunPlugin())` without attaching it to the
        parent's pipeline too. Any `fixed_meta` given is injected into
        every record the child produces, via an internal `ContextPlugin`.
        Inherits this logger's `content_policy`.
        """
        # No transports passed, so the child starts no queue of its own; it takes this logger's.
        child_logger = Logger(
            f"{self.name}.{name}",
            level=self._level,
            content_policy=self.content_policy,
        )
        # Descendants inherit `meta=` but not a child's own `fixed_meta` (decision D1, as in 2.0.0).
        child_logger._base_meta = self._base_meta
        if self._base_meta or fixed_meta:
            child_logger.use(ContextPlugin(**{**self._base_meta, **fixed_meta}))
        # Share the parent's dispatcher rather than starting a second queue and thread: both
        # loggers write to the same transport instances, so their records belong on one queue.
        child_logger._share_with(self)
        return child_logger

    def flush(self, timeout: float | None = None) -> bool:
        """Wait for every record already submitted for async dispatch to
        finish writing, then flush each transport's own internal buffer
        (e.g. `BatchingTransport`) — without closing anything.

        Unlike `close()`, safe to call repeatedly mid-lifetime: this is
        what `with_lambda`/`with_cloud_function`/`with_azure_function` call
        before a serverless invocation returns, since a warm container
        reuses this same `Logger`/its transports on the next invocation.

        Returns whether the async queue (if any) fully drained within
        `timeout`; always `True` when `async_dispatch` wasn't enabled, since
        dispatch already happened synchronously before this call.
        """
        if self._dispatcher is None:
            return True
        return bool(self._dispatcher.flush(timeout))

    async def flush_async(self, timeout: float | None = None) -> bool:
        """`asyncio`-friendly `flush()`: awaits the drain instead of blocking the calling thread.
        See `flush()`."""
        # imported here, not at module level: `asyncio` costs ~12 ms to import
        import asyncio

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.flush, timeout)

    def close(self, timeout: float | None = 5.0) -> None:
        """Stop async dispatch (draining any queued records first, up to `timeout` seconds) and
        close every attached transport. Call on process shutdown: after this, the transports may
        release resources a later log call would need, so don't call it on a `Logger` you intend
        to keep using (see `flush()` for that case).
        """
        self._open[0] = False
        if self._dispatcher is not None:
            self._dispatcher.close(timeout)
        for transport in self.transports:
            shutdown.mark_closed(transport)

    #: Caps `_opt_cache` (see `opt()`) so a caller that computes `depth`
    #: dynamically per call — an unusual pattern, but not one worth trusting
    #: not to happen — can't grow it without bound; past this, `opt()` just
    #: stops caching and returns a fresh, uncached `OptLogger` each time.
    _OPT_CACHE_MAX = 32

    def opt(self, *, lazy: bool = False, depth: int | None = None) -> OptLogger:
        """A view of this logger with per-call options — `lazy=True` to
        defer expensive `meta` values until the record is really emitted,
        `depth=N` to report the caller `N` frames up the stack. See
        `OptLogger`.

        The common idiom is calling this inline on every log call
        (`logger.opt(lazy=True).debug(...)`), so the returned `OptLogger` —
        itself immutable once built — is cached per `(lazy, depth)`
        combination instead of allocated fresh every time; a benign race
        between two threads caching the same combination at once just
        produces one short-lived extra instance, never a wrong one.
        """
        key = (lazy, depth)
        cached = self._opt_cache.get(key)
        if cached is not None:
            return cached
        view = OptLogger(self, lazy=lazy, depth=depth)
        if len(self._opt_cache) < self._OPT_CACHE_MAX:
            self._opt_cache[key] = view
        return view

    def _track_retries(self, level: Level, meta: dict[str, Any]) -> None:
        """Stamp `meta.retry_count` on a tool `.action()` that reopens a call
        which hasn't succeeded yet, and end the chain on a successful
        `.observation()` for it. Only records naming their tool in `meta.tool`
        take part."""
        tool = meta.get("tool")
        if not isinstance(tool, str) or not tool:
            return
        call_id = meta.get("tool_call_id")
        call_id = call_id if isinstance(call_id, str) else None
        kind = meta.get("kind")
        if kind == "action":
            count = self._retries.opened(current_span_id(), tool, call_id)
            if count > 0:
                meta.setdefault("retry_count", count)
        elif kind == "observation" and level < Level.ERROR and "error" not in meta:
            self._retries.succeeded(current_span_id(), tool, call_id)

    def _local_redactor(self) -> LocalRedactor:
        """Chain every plugin's `redact_local` into one function for
        `diagnose` mode. A plugin whose hook raises fails closed: the value
        is masked rather than shown, since the alternative is leaking exactly
        what that plugin exists to hide."""
        plugins = list(self.plugins)

        def redact(name: str, text: str) -> str:
            for plugin in plugins:
                try:
                    text = plugin.redact_local(name, text)
                except Exception:
                    return "<redaction failed>"
            return text

        return redact

    def _capture_exception(self, meta: dict[str, Any]) -> None:
        """Pop `diagnose` and `exc_info` from `meta` and put the formatted traceback in
        `meta["stack"]`. A malformed `exc_info` is reported and ignored: a log call must not crash."""
        diagnose = bool(meta.pop("diagnose", False))
        if diagnose:
            meta.setdefault("exc_info", True)
        if "exc_info" not in meta:
            return
        exc_info = meta.pop("exc_info")
        try:
            stack = format_exc_info(
                exc_info,
                diagnose=diagnose,
                redact_local=self._local_redactor() if diagnose else None,
            )
        except Exception:
            _logger.warning(
                "Logger: ignoring exc_info=%r — it must be True, an exception instance, "
                "or a (type, value, traceback) tuple",
                exc_info,
            )
            stack = None
        if stack is not None:
            meta["stack"] = stack

    def _notify_error(self, plugin: Plugin, exc: Exception, record: LogRecord) -> None:
        # a broken error handler must not crash logging either
        with contextlib.suppress(Exception):
            plugin.on_error(exc, record)

    def _run_after_log(self, record: LogRecord) -> None:
        """Run the `after_log` hook of every plugin that defines one, once the transports have
        written `record`. A hook that raises goes to that plugin's `on_error`."""
        for plugin in self._after_plugins:
            try:
                plugin.after_log(record)
            except Exception as exc:
                self._notify_error(plugin, exc, record)

    def _write_host(self, record: LogRecord, after: Callable[[LogRecord], None] | None) -> None:
        """Write `record` straight to Python-only transports on this thread, handing them the
        caller's own dict as 2.0.0 did, but only when it is plain JSON data (`is_json_safe`), so
        it equals the copy the core would have made. Going through the core would turn the record
        into its JSON form and back for each transport, which is the whole cost on this path; a
        record with a cycle, NaN or a callable still goes through the core, which sanitises it
        for the transport. The lock keeps
        writes one at a time, as the core's transport lock does; a failing transport is reported
        and never stops the others."""
        if not self._open[0]:
            return
        with self._write_lock:
            for transport in self.transports:
                try:
                    transport.write(transport.format(record), record)
                except Exception as exc:
                    _warn(f"a transport failed to write a record — {type(exc).__name__}: {exc}")
        if after is not None:
            after(record)

    def _dispatch(self, record: LogRecord) -> LogRecord:
        """Hand `record` to the Rust dispatcher (queued, or written now with
        `async_dispatch=False`) and arrange for `after_log` to run once it is written. Returns the
        record the caller gets back: the same one, except that a logger with no transports chains
        it here, because there is no dispatcher to do it at write time."""
        after = self._run_after_log if self._after_plugins else None
        dispatcher = self._dispatcher
        chain = self._chain
        try:
            if dispatcher is None:
                if chain is not None:
                    record = chain.stamp(record)  # type: ignore[attr-defined]
                if after is not None:
                    after(record)
            elif self._async:
                dispatcher.emit(record, after, chain)
            elif self._host_only and chain is None and _native.is_json_safe(record):
                self._write_host(record, after)
            else:
                dispatcher.write_now(record, after, chain)
        except Exception:
            # a record that cannot be written (say, a dictionary key of an unsupported type) must
            # not crash the caller
            _logger.exception("Logger %s: failed to hand a log record to its transports", self.name)
        return record

    def _log(
        self,
        level: Level,
        message: str,
        meta: dict[str, Any],
        *,
        lazy: bool = False,
        depth: int | None = None,
        llm: LLMBlock | None = None,
    ) -> LogRecord | None:
        if level < self._level or (_toggle._rules and not is_enabled(self.name)):
            return None

        if lazy:
            meta = resolve_lazy(meta)

        if meta:
            if "tool" in meta:
                self._track_retries(level, meta)
            if depth is not None:
                caller = caller_info(depth)
                if caller is not None:
                    meta.setdefault("caller", caller)
            if "diagnose" in meta or "exc_info" in meta:
                self._capture_exception(meta)
        elif depth is not None:
            caller = caller_info(depth)
            if caller is not None:
                meta.setdefault("caller", caller)

        record: LogRecord = {  # type: ignore[typeddict-item]
            "schema_version": SCHEMA_VERSION,
            "timestamp": _now(),
            "level": _LEVEL_NAMES[level],
            "logger": self.name,
            "message": message,
            "meta": meta,
        }
        if llm is not None:
            record["llm"] = llm

        bound_context = _current_context.get()
        if bound_context:
            record["meta"] = {**bound_context, **meta}

        parent_span_id = _current_span_id.get()
        if parent_span_id is not None:
            record["meta"].setdefault("parent_span_id", parent_span_id)

        if self.content_policy != "full" and not _CONTENT_FIELDS.isdisjoint(record["meta"]):
            apply_content_policy(record["meta"], self.content_policy)

        if self.plugins:
            for plugin in self.plugins:
                try:
                    result = plugin.before_log(record)
                except Exception as exc:
                    self._notify_error(plugin, exc, record)
                    if plugin.fails_closed:
                        # a plugin that protects data failed: withhold the record rather than
                        # write it unprotected (decision D20)
                        _logger.warning(
                            "%s failed (%s: %s); the record was withheld, not written unprotected",
                            type(plugin).__name__,
                            type(exc).__name__,
                            exc,
                        )
                        return None
                    continue
                if result is None:
                    return None
                record = result

        return self._dispatch(record)

    # `message: str, /` (positional-only) on every method below: a caller
    # passing `**meta` where `meta` happens to contain a `"message"` key
    # (e.g. forwarding an adversarial or framework-supplied dict) would
    # otherwise collide with the `message` parameter and raise
    # `TypeError: got multiple values for argument 'message'`, crashing the
    # caller — exactly what the plugin pipeline's hypothesis tests assert
    # never happens (see `tests/test_plugin_pipeline_properties.py`).
    def trace(self, message: str, /, **meta: Any) -> LogRecord | None:
        """Log at `TRACE`. Returns the emitted record, or `None` if filtered
        by level or dropped by a plugin."""
        return self._log(Level.TRACE, message, meta)

    def debug(self, message: str, /, **meta: Any) -> LogRecord | None:
        """Log at `DEBUG`. Returns the emitted record, or `None` if filtered
        by level or dropped by a plugin."""
        return self._log(Level.DEBUG, message, meta)

    def info(self, message: str, /, **meta: Any) -> LogRecord | None:
        """Log at `INFO`. Returns the emitted record, or `None` if filtered
        by level or dropped by a plugin."""
        return self._log(Level.INFO, message, meta)

    def warn(self, message: str, /, **meta: Any) -> LogRecord | None:
        """Log at `WARN`. Returns the emitted record, or `None` if filtered
        by level or dropped by a plugin."""
        return self._log(Level.WARN, message, meta)

    def error(self, message: str, /, **meta: Any) -> LogRecord | None:
        """`exc_info=` (an exception instance, `True` for the exception
        currently being handled, or an explicit `(type, value, traceback)`
        tuple — the same shapes stdlib `logging` accepts) formats a
        traceback into `meta["stack"]` and is otherwise not kept in `meta`
        as-is, since a raw exception object isn't serializable. Every
        `Logger` method accepts it, not just this one.

        `diagnose=True` additionally writes each frame's local variable
        values into that traceback (and implies `exc_info=True` if none was
        given). **Off by default, and it can leak sensitive data** —
        whatever a local holds (a password, a token, a request body) ends up
        in the log, so keep it off in production. Locals are passed through
        the registered `RedactPlugin`/`PIIRedactPlugin` before the traceback
        is formatted, but that only masks what those plugins are configured
        to recognize (by variable name, or by PII pattern in the value)."""
        return self._log(Level.ERROR, message, meta)

    def fatal(self, message: str, /, **meta: Any) -> LogRecord | None:
        """Log at `FATAL`. Returns the emitted record, or `None` if filtered
        by level or dropped by a plugin."""
        return self._log(Level.FATAL, message, meta)

    def thought(self, message: str, /, **meta: Any) -> LogRecord | None:
        """`.info()` tagged `meta.kind = "thought"` — an agent's internal
        reasoning step, for harness/agentic tracing."""
        return self._log(Level.INFO, message, {"kind": "thought", **meta})

    def action(self, message: str, /, **meta: Any) -> LogRecord | None:
        """`.info()` tagged `meta.kind = "action"` — an agent taking an
        action (a tool call, an LLM request), for harness/agentic tracing."""
        return self._log(Level.INFO, message, {"kind": "action", **meta})

    def observation(self, message: str, /, **meta: Any) -> LogRecord | None:
        """`.info()` tagged `meta.kind = "observation"` — the result an
        agent observed from an action, for harness/agentic tracing."""
        return self._log(Level.INFO, message, {"kind": "observation", **meta})

    def decision(self, message: str, /, **meta: Any) -> LogRecord | None:
        """`.info()` tagged `meta.kind = "decision"` — an agent's concluding
        decision for a step or run, for harness/agentic tracing."""
        return self._log(Level.INFO, message, {"kind": "decision", **meta})

    def llm_call(
        self,
        message: str = "llm_call",
        /,
        *,
        model: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: float | None = None,
        latency_ms: float | None = None,
        finish_reason: str | None = None,
        **meta: Any,
    ) -> LogRecord | None:
        """Log one LLM call as an `.action()` carrying the record's first-class
        `llm` block (`model`, `tokens_in`, `tokens_out`, `cost_usd`,
        `latency_ms`, `finish_reason`). Those fields are what cost and latency
        dashboards read, and what `OTLPTransport` exports as the standard token
        and model attributes. Any you leave out are simply absent; one of the
        wrong type is dropped with a warning rather than raising.

        Extra keyword arguments go in `meta` as usual — e.g. `provider="openai"`.
        Don't put prompt or completion text in `meta` unless you mean every
        transport to receive it.
        """
        llm = build_llm_block(
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
        )
        return self._log(Level.INFO, message, {"kind": "action", **meta}, llm=llm)

    def span(
        self,
        name: str,
        /,
        *,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        capture_state: Callable[[], Any] | None = None,
        **meta: Any,
    ) -> SpanContext:
        """`with agent_log.span("call_llm"):` — on exit, emits one record
        carrying `meta.span_id` and `meta.duration_ms`; every record logged
        inside the block (through any method) is automatically stamped with
        `meta.parent_span_id` pointing at this span, so nested/sub-agent
        calls reconstruct their exact nesting when sorted by
        `span_id`/`parent_span_id`. Still emits its record — at `ERROR`,
        with `meta.error` set — if the block raises; the exception itself
        propagates unchanged.

        `span_id`/`parent_span_id` normally auto-generate/auto-nest; pass
        them explicitly to adopt an id handed in from elsewhere (see
        `logquill.adapters.langchain.LangChainAdapter` for an example).

        `capture_state=lambda: {...}` is called on entering and on leaving the
        block, and what changed between the two is recorded as
        `meta.state_diff` (`{"before": ..., "after": ...}`, only the keys that
        changed when both are dicts; omitted if nothing did). The values are
        deep-copied, so an in-place mutation is seen; a state that can't be
        copied, or a callable that raises, just means no `state_diff`. It's
        stored in `meta`, so a redaction plugin that recurses (`PIIRedactPlugin`)
        sees it, but `RedactPlugin` only matches top-level keys.
        """
        return SpanContext(
            self,
            name,
            span_id=span_id,
            parent_span_id=parent_span_id,
            capture_state=capture_state,
            **meta,
        )
