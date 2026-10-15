from __future__ import annotations

import threading

# Copy-on-write: writers swap in a new dict under the lock, readers just read
# the current reference. The hot path (`is_enabled` on every log call) takes
# no lock, and with no rules set it's a single truthiness check.
_rules: dict[str, bool] = {}
_lock = threading.Lock()


def disable(name: str = "") -> None:
    """Turn off every `Logger` whose name is `name` or nested under it
    (`"mylib"` covers `"mylib"` and `"mylib.http"`, not `"mylib2"`), so its
    log calls become no-ops. `disable("")` turns off everything.

    This is for libraries that use LogQuill internally: call it once at
    import so the library is silent by default instead of polluting its
    host application's logs, and let the application opt back in:

        # mylib/__init__.py
        import logquill
        logquill.disable(__name__)

        # the application, if it wants mylib's logs
        logquill.enable("mylib")

    The most specific rule wins: `disable("mylib")` followed by
    `enable("mylib.http")` silences mylib except `mylib.http`.
    """
    _set(name, False)


def enable(name: str = "") -> None:
    """Undo `disable()` for `name` and everything nested under it. See
    `disable()` for how rules combine."""
    _set(name, True)


def is_enabled(name: str) -> bool:
    """Whether a logger called `name` currently emits records — i.e. the
    most specific `enable()`/`disable()` rule covering it, or `True` if
    none does."""
    rules = _rules
    if not rules:
        return True
    candidate = name
    while True:
        verdict = rules.get(candidate)
        if verdict is not None:
            return verdict
        if not candidate:
            return True
        candidate = candidate.rpartition(".")[0]


def _set(name: str, enabled: bool) -> None:
    global _rules
    if not isinstance(name, str):
        raise TypeError(
            f"disable()/enable() take a logger name such as __name__, got {type(name).__name__}"
        )
    with _lock:
        _rules = {**_rules, name: enabled}


def _reset() -> None:
    """Drop every rule. For tests."""
    global _rules
    with _lock:
        _rules = {}
