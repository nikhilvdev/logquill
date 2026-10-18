import importlib
import subprocess
import sys

import pytest

import logquill

# stdlib modules that are expensive to import and that only some transports and
# plugins need — `import logquill` must not pull any of them in
_HEAVY_STDLIB = ["asyncio", "urllib.request", "ssl", "smtplib", "sqlite3", "http.client"]


def _run(code: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    )
    return result.stdout.strip()


def test_importing_logquill_does_not_load_heavy_stdlib_modules() -> None:
    loaded = _run(
        f"import sys, logquill\nprint(','.join(m for m in {_HEAVY_STDLIB!r} if m in sys.modules))"
    )

    assert loaded == "", f"`import logquill` now imports: {loaded}"


def test_a_lazy_name_loads_its_module_on_first_use() -> None:
    result = _run(
        "import sys, logquill\n"
        "assert 'logquill.transports.http_transport' not in sys.modules\n"
        "cls = logquill.HTTPTransport\n"
        "assert 'logquill.transports.http_transport' in sys.modules\n"
        "assert 'HTTPTransport' in vars(logquill)  # cached, so __getattr__ runs once\n"
        "print(cls.__name__)"
    )

    assert result == "HTTPTransport"


def test_every_exported_name_resolves() -> None:
    unresolved = [name for name in logquill.__all__ if not hasattr(logquill, name)]

    assert unresolved == []


def test_from_import_works_for_lazy_names() -> None:
    from logquill import SlackAlertPlugin, SQLiteTransport

    assert SlackAlertPlugin.__module__ == "logquill.plugins.slack_alert_plugin"
    assert SQLiteTransport.__module__ == "logquill.transports.sql.sqlite_transport"


def test_lazy_exports_point_at_the_module_that_defines_them() -> None:
    wrong = []
    for name, module in logquill._LAZY_EXPORTS.items():
        if not hasattr(importlib.import_module(module), name):
            wrong.append(f"{name} is not defined in {module}")

    assert wrong == []


def test_lazy_names_are_listed_in_all_and_dir() -> None:
    assert set(logquill._LAZY_EXPORTS) <= set(logquill.__all__)
    assert set(logquill._LAZY_EXPORTS) <= set(dir(logquill))


def test_an_unknown_attribute_still_raises_attribute_error() -> None:
    with pytest.raises(AttributeError, match="no attribute 'NotAThing'"):
        logquill.NotAThing  # noqa: B018


def test_config_http_transport_type_still_builds() -> None:
    logger = logquill.load_config(
        {"transports": [{"type": "http", "options": {"url": "http://localhost:1/ingest"}}]}
    )

    assert type(logger.transports[0]).__name__ == "HTTPTransport"
    logger.close()


def test_config_unknown_type_still_lists_http_among_the_built_ins() -> None:
    with pytest.raises(ValueError, match="http"):
        logquill.load_config({"transports": [{"type": "nope"}]})
