from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Any

from logquill.core.logger import Logger
from logquill.formatters import Formatter, JSONFormatter, LogfmtFormatter, TextFormatter
from logquill.plugins.context_plugin import ContextPlugin
from logquill.plugins.pii_redact_plugin import PIIRedactPlugin
from logquill.plugins.plugin import Plugin
from logquill.plugins.redact_plugin import RedactPlugin
from logquill.plugins.run_plugin import RunPlugin
from logquill.plugins.sampling_plugin import SamplingPlugin
from logquill.plugins.tamper_evident_plugin import TamperEvidentPlugin
from logquill.plugins.trace_context_plugin import TraceContextPlugin
from logquill.transports.console_transport import ConsoleTransport
from logquill.transports.file_transport import FileTransport
from logquill.transports.http_transport import HTTPTransport
from logquill.transports.transport import Transport

#: Shortcuts for the zero-required-dependency transports/plugins, so common
#: configs don't need a fully-qualified class path. Anything else — every
#: cloud/SQL/NoSQL/queue transport, the alerting plugins, framework
#: adapters, or your own subclass — goes through `"class"` instead (see
#: `load_config`). This is deliberately a small, curated list, not an
#: attempt to cover the whole catalog: the ones here are safe to construct
#: from plain config with no optional dependency surprising the caller.
_TRANSPORT_TYPES: dict[str, type[Transport]] = {
    "console": ConsoleTransport,
    "file": FileTransport,
    "http": HTTPTransport,
}

_PLUGIN_TYPES: dict[str, type[Plugin]] = {
    "context": ContextPlugin,
    "redact": RedactPlugin,
    "sampling": SamplingPlugin,
    "trace_context": TraceContextPlugin,
    "run": RunPlugin,
    "pii_redact": PIIRedactPlugin,
    "tamper_evident": TamperEvidentPlugin,
}


#: Built-in formatters selectable by name in a transport's `options`
#: (`{"formatter": "logfmt"}`), since a config file can't hold an instance.
_FORMATTER_TYPES: dict[str, type[Formatter]] = {
    "json": JSONFormatter,
    "text": TextFormatter,
    "logfmt": LogfmtFormatter,
}


def _resolve_class(entry: dict[str, Any], registry: dict[str, type]) -> type:
    if "class" in entry:
        dotted = entry["class"]
        module_name, separator, class_name = dotted.rpartition(".")
        if not separator:
            raise ValueError(
                f"'class' must be a fully-qualified dotted path (e.g. "
                f"'logquill.transports.cloud.datadog_transport.DatadogTransport'), got {dotted!r}"
            )
        module = importlib.import_module(module_name)
        try:
            return getattr(module, class_name)  # type: ignore[no-any-return]
        except AttributeError:
            raise ValueError(
                f"{dotted!r}: module {module_name!r} has no attribute {class_name!r}"
            ) from None
    if "type" in entry:
        try:
            return registry[entry["type"]]
        except KeyError:
            known = ", ".join(sorted(registry))
            raise ValueError(
                f"Unknown type {entry['type']!r} — built-in types are: {known}. "
                "Use 'class': '<module.path.ClassName>' for anything else."
            ) from None
    raise ValueError(f"Each transport/plugin entry needs a 'type' or 'class' key, got {entry!r}")


def _build(entries: list[dict[str, Any]] | None, registry: dict[str, type]) -> list[Any]:
    built = []
    for entry in entries or []:
        cls = _resolve_class(entry, registry)
        built.append(cls(**_resolve_options(entry.get("options", {}))))
    return built


def _resolve_options(options: dict[str, Any]) -> dict[str, Any]:
    formatter = options.get("formatter")
    if not isinstance(formatter, str):
        return options
    try:
        return {**options, "formatter": _FORMATTER_TYPES[formatter]()}
    except KeyError:
        known = ", ".join(sorted(_FORMATTER_TYPES))
        raise ValueError(
            f"Unknown formatter {formatter!r} — built-in formatters are: {known}."
        ) from None


def load_config(data: dict[str, Any], *, name: str = "app") -> Logger:
    """Build a `Logger` from an already-parsed config dict — the same
    shape `logger_from_file`/`logger_from_env` parse JSON/YAML into:

        {
          "name": "app",                        # optional, defaults to `name`
          "level": "INFO",
          "transports": [
            {"type": "console"},
            {"type": "file", "options": {"path": "app.log"}},
            {"class": "logquill.transports.cloud.datadog_transport.DatadogTransport",
             "options": {"api_key": "..."}}
          ],
          "plugins": [
            {"type": "context", "options": {"service": "api"}},
            {"type": "sampling", "options": {"rate": 0.1}}
          ],
          "async_dispatch": true,
          "max_queue_size": 10000,
          "backpressure": "drop_oldest",
          "flush_at_exit": true
        }

    A transport's `options` may name a built-in formatter as a string —
    `{"type": "console", "options": {"formatter": "text"}}` — one of
    `"json"` (the default), `"text"` or `"logfmt"`.

    Each transport/plugin entry needs either `"type"` (a built-in shortcut —
    see the module-level registries above) or `"class"` (a fully-qualified
    dotted path, imported and instantiated the way `logging.config.
    dictConfig` resolves a `class` key — the same trust boundary: only use
    this with config you trust, the same as any other deployment config).
    `"options"` becomes that class's constructor keyword arguments.

    `"async_dispatch"`/`"max_queue_size"`/`"backpressure"`/`"flush_at_exit"` are optional and
    map directly onto `Logger`'s constructor arguments of the same name —
    see there for what each does.
    """
    logger_name = data.get("name", name)
    level = data.get("level", "INFO")
    transports = _build(data.get("transports"), _TRANSPORT_TYPES)
    plugins = _build(data.get("plugins"), _PLUGIN_TYPES)
    return Logger(
        logger_name,
        level=level,
        transports=transports,
        plugins=plugins,
        async_dispatch=data.get("async_dispatch", False),
        max_queue_size=data.get("max_queue_size", 10_000),
        backpressure=data.get("backpressure", "drop_oldest"),
        flush_at_exit=data.get("flush_at_exit", True),
    )


def logger_from_file(path: str | Path, *, name: str = "app") -> Logger:
    """Load a `Logger` from a `.json`/`.yaml`/`.yml` file — see `load_config`
    for the shape. YAML needs the optional `PyYAML` dependency
    (`pip install logquill[yaml]`); JSON needs nothing beyond the stdlib.
    """
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if file_path.suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "logger_from_file(...) with a .yaml/.yml file requires the optional "
                "`PyYAML` dependency — install with `pip install logquill[yaml]`."
            ) from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    return load_config(data, name=name)


def logger_from_env(*, prefix: str = "LOGQUILL_", name: str = "app") -> Logger:
    """Build a `Logger` from environment variables.

    `{prefix}CONFIG_FILE`, if set, is loaded via `logger_from_file` first —
    the full transport/plugin config isn't practical to express as flat env
    vars. `{prefix}LEVEL`, if set, is applied last and always wins, even
    over a level set in the config file — the common convention of letting
    an env var override a file for the one field ops teams actually reach
    for at deploy time.
    """
    config_file = os.environ.get(f"{prefix}CONFIG_FILE")
    logger = logger_from_file(config_file, name=name) if config_file else Logger(name)

    level = os.environ.get(f"{prefix}LEVEL")
    if level:
        logger.set_level(level)
    return logger
