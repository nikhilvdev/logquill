from __future__ import annotations

from logquill.core.logger import Logger


class LogQuillAdapter:
    """Base class for framework tracing adapters.

    A concrete adapter subclasses this, holds a reference to the `Logger` to
    forward events onto (`self.log`), and overrides only the events its
    framework actually emits — translating them into `.thought()/.action()/
    .observation()/.decision()` and `.span()` calls. This is always meant to
    be a thin mapping from the framework's native event shape onto
    LogQuill's, never a reimplementation of tracing logic per framework.

    See `logquill.adapters.langchain.LangChainAdapter` for the reference
    implementation.
    """

    def __init__(self, agent_log: Logger) -> None:
        """`agent_log` is the `Logger` (typically `.child(...)` with a
        `RunPlugin` attached) every translated event is forwarded onto."""
        self.log = agent_log
