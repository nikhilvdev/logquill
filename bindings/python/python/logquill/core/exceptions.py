from __future__ import annotations

import logging
import sys
import traceback
from types import TracebackType
from typing import Callable, Literal, Union

_logger = logging.getLogger("logquill")

#: `(local variable name, its repr) -> the text to show instead` — how
#: `diagnose` mode gives redaction plugins a chance to mask a captured local
#: before it's ever formatted into the traceback.
LocalRedactor = Callable[[str, str], str]

#: Longest repr shown for one captured local; the rest is cut with `...`.
_MAX_LOCAL_REPR = 200

#: A module-level frame's "locals" are the module's globals — mostly imported
#: modules and function/class definitions, which are noise, not state.
_NOISE_PREFIXES = ("<module ", "<function ", "<class ", "<built-in ", "<bound method ")

_diagnose_warned = False

# `Literal[True]` rather than `bool`: `False` behaves identically to `None`
# (both mean "nothing to format", handled by the `not exc_info` check below),
# so it isn't a distinct case worth widening the type for — and keeping it
# out lets the branches below narrow cleanly under `mypy --strict`.
ExcInfoArg = Union[
    Literal[True],
    BaseException,
    "tuple[type[BaseException], BaseException, TracebackType | None]",
    None,
]


def format_exc_info(
    exc_info: ExcInfoArg,
    *,
    diagnose: bool = False,
    redact_local: LocalRedactor | None = None,
) -> str | None:
    """Render `exc_info` as a formatted traceback string, or `None` if
    there's nothing to format. Accepts the same shapes stdlib `logging`
    does, so `logger.error("failed", exc_info=e)` reads exactly like the
    `logging` module's own `exc_info=` kwarg:

    - `True` — format the exception currently being handled (`sys.exc_info()`)
    - an exception instance — format it and its own traceback
    - an explicit `(type, value, traceback)` tuple
    - falsy (`False`/`None`, the default) — nothing to format

    `diagnose=True` also prints each frame's local variables under its
    source line, which makes a failure far easier to debug — and can leak
    secrets, since whatever a local holds (a password, a token, a whole
    request body) lands in the log. Leave it off in production. When it is
    on, every captured local is passed through `redact_local` *before* the
    traceback is formatted, so a redaction plugin's rules apply to it;
    `Logger` wires that up from its plugins. Without a `redact_local`, values
    are shown as-is.
    """
    if not exc_info:
        return None

    exc_type: type[BaseException] | None
    exc_value: BaseException | None
    exc_tb: TracebackType | None

    if exc_info is True:
        exc_type, exc_value, exc_tb = sys.exc_info()
        if exc_type is None:
            return None
    elif isinstance(exc_info, BaseException):
        exc_type, exc_value, exc_tb = type(exc_info), exc_info, exc_info.__traceback__
    else:
        exc_type, exc_value, exc_tb = exc_info

    if diagnose and exc_value is not None:
        _warn_diagnose_once()
        try:
            return _format_with_locals(exc_type, exc_value, exc_tb, redact_local)
        except Exception:
            # a local whose `__repr__` raises, say — fall back to the plain
            # traceback, which shows no values and so can't leak any
            _logger.debug("diagnose: couldn't capture locals, using a plain traceback")

    return "".join(traceback.format_exception(exc_type, exc_value, exc_tb))


def _warn_diagnose_once() -> None:
    global _diagnose_warned
    if not _diagnose_warned:
        _diagnose_warned = True
        _logger.warning(
            "diagnose=True writes local variable values into log records, which can leak "
            "sensitive data (passwords, tokens, personal data) — keep it off in production, "
            "and use RedactPlugin/PIIRedactPlugin if you must run it there"
        )


def _format_with_locals(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: TracebackType | None,
    redact_local: LocalRedactor | None,
) -> str:
    formatted = traceback.TracebackException(exc_type, exc_value, exc_tb, capture_locals=True)
    _scrub_locals(formatted, redact_local, set())
    return "".join(formatted.format())


def _scrub_locals(
    formatted: traceback.TracebackException,
    redact_local: LocalRedactor | None,
    seen: set[int],
) -> None:
    """Redact and trim the locals captured on `formatted` and on every
    exception chained to it, in place — before anything is formatted."""
    if id(formatted) in seen:
        return
    seen.add(id(formatted))

    for frame in formatted.stack:
        captured = frame.locals or {}
        kept: dict[str, str] = {}
        for name, text in captured.items():
            if name.startswith("__") or text.startswith(_NOISE_PREFIXES):
                continue
            if redact_local is not None:
                text = redact_local(name, text)
            kept[name] = text if len(text) <= _MAX_LOCAL_REPR else text[:_MAX_LOCAL_REPR] + "..."
        frame.locals = kept

    chained = [formatted.__cause__, formatted.__context__]
    chained.extend(getattr(formatted, "exceptions", None) or [])  # exception groups (3.11+)
    for other in chained:
        if other is not None:
            _scrub_locals(other, redact_local, seen)
