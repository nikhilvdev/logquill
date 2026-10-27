"""Shared plumbing for the `logquill.instrument.*` patchers: swap one method
or function out for a wrapper, remember how to put it back, and never let a
bug in the wrapper break the real call it wraps.
"""

from __future__ import annotations

import logging
import types
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

_logger = logging.getLogger("logquill")

T = TypeVar("T")


@dataclass
class Patch:
    """One reversible attribute swap."""

    target: Any
    name: str
    original: Any

    def revert(self) -> None:
        """Puts `original` back; for an instance patch (nothing was there before) removes it."""
        if self.original is _ABSENT:
            delattr(self.target, self.name)
        else:
            setattr(self.target, self.name, self.original)


_ABSENT = object()


def apply_to_instance(obj: Any, name: str, build_wrapper: Callable[[Any], Any]) -> Patch:
    """Like `apply`, but on one object only: `build_wrapper` gets the class's function (so it takes
    `self` first, as the class-level wrappers do) and the result is bound to `obj`. Other objects of
    the same class are untouched, which is what per-client instrumentation is for."""
    setattr(obj, name, types.MethodType(build_wrapper(getattr(type(obj), name)), obj))
    return Patch(obj, name, _ABSENT)


def apply(target: Any, name: str, build_wrapper: Callable[[Any], Any]) -> Patch:
    """Replaces `target.name` with `build_wrapper(current_value)` and returns
    a `Patch` that can put it back."""
    original = getattr(target, name)
    setattr(target, name, build_wrapper(original))
    return Patch(target, name, original)


def record_safely(do_record: Callable[[], None]) -> None:
    """Runs `do_record` (which logs one call's fields), swallowing and
    logging any exception. A bug in instrumentation must never surface as a
    failure of the API call it's instrumenting — the whole point of
    `instrument()` is that call sites don't change, including their error
    handling."""
    try:
        do_record()
    except Exception:
        _logger.exception("logquill.instrument: failed to record a call")


class Instrumenter:
    """One `logquill.instrument.<provider>` entry: calling it patches,
    `.uninstrument()` puts everything back. Calling it twice without an
    intervening `.uninstrument()` raises, so a wrapper is never wrapped
    twice — each instrumented method would otherwise fire this provider's
    `llm_call` twice per real call, once per layer.
    """

    def __init__(self, name: str, apply_patches: Callable[..., list[Patch]]) -> None:
        """`name` is used only in the "already active" error message.
        `apply_patches` does the actual patching (typically importing the
        provider SDK lazily and calling `apply()` once per method) and
        returns the `Patch`es to revert later."""
        self._name = name
        self._apply_patches = apply_patches
        self._patches: list[Patch] = []
        self._global = False

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        """Patches the provider SDK. See the concrete module (e.g.
        `logquill.instrument.anthropic`) for what arguments it takes. With `client=...` only that
        client is patched, and several clients can be added one call after another; the global
        patch cannot be mixed with them, since a call would be logged by both."""
        per_client = kwargs.get("client") is not None
        if self._global or (self._patches and not per_client):
            raise RuntimeError(
                f"logquill.instrument.{self._name} is already active — call "
                f".uninstrument() first if you want to change its logger or options"
            )
        self._patches.extend(self._apply_patches(*args, **kwargs))
        self._global = not per_client

    def uninstrument(self) -> None:
        """Restores every method this patched. Safe to call even when not
        currently instrumented."""
        while self._patches:
            self._patches.pop().revert()
        self._global = False

    @property
    def active(self) -> bool:
        """Whether `instrument()` has been called without a matching
        `.uninstrument()` since."""
        return bool(self._patches)
