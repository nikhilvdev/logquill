from __future__ import annotations

import sys
from pathlib import Path

import pytest

from logquill.config import load_config, logger_from_env, logger_from_file
from logquill.core.levels import Level
from logquill.plugins.context_plugin import ContextPlugin
from logquill.transports.console_transport import ConsoleTransport
from logquill.transports.file_transport import FileTransport


def test_load_config_builds_level_transports_and_plugins() -> None:
    logger = load_config(
        {
            "name": "app",
            "level": "WARN",
            "transports": [{"type": "console", "options": {"colorize": False}}],
            "plugins": [{"type": "context", "options": {"service": "api"}}],
        }
    )

    assert logger.name == "app"
    assert logger.level == Level.WARN
    assert len(logger.transports) == 1
    assert isinstance(logger.transports[0], ConsoleTransport)
    assert logger.transports[0].colorize is False
    assert len(logger.plugins) == 1
    assert isinstance(logger.plugins[0], ContextPlugin)


def test_load_config_defaults_name_and_level() -> None:
    logger = load_config({}, name="fallback")

    assert logger.name == "fallback"
    assert logger.level == Level.INFO
    assert logger.transports == []
    assert logger.plugins == []


def test_load_config_name_key_overrides_the_name_kwarg() -> None:
    logger = load_config({"name": "from-config"}, name="fallback")

    assert logger.name == "from-config"


def test_load_config_file_transport_via_type(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    logger = load_config({"transports": [{"type": "file", "options": {"path": str(log_path)}}]})

    record = logger.info("hello")
    logger.close()

    assert record is not None
    assert isinstance(logger.transports[0], FileTransport)
    assert log_path.exists()


def test_load_config_class_key_resolves_a_dotted_path(tmp_path: Path) -> None:
    log_path = tmp_path / "app.log"
    logger = load_config(
        {
            "transports": [
                {
                    "class": "logquill.transports.file_transport.FileTransport",
                    "options": {"path": str(log_path)},
                }
            ]
        }
    )

    logger.info("hello")
    logger.close()

    assert isinstance(logger.transports[0], FileTransport)
    assert log_path.exists()


def test_load_config_unknown_type_raises_a_helpful_error() -> None:
    with pytest.raises(ValueError, match="Unknown type 'nonsense'"):
        load_config({"transports": [{"type": "nonsense"}]})


def test_load_config_class_without_a_dot_raises() -> None:
    with pytest.raises(ValueError, match="fully-qualified dotted path"):
        load_config({"transports": [{"class": "NotDotted"}]})


def test_load_config_class_with_missing_attribute_raises() -> None:
    with pytest.raises(ValueError, match="has no attribute 'DoesNotExist'"):
        load_config({"transports": [{"class": "logquill.transports.file_transport.DoesNotExist"}]})


def test_load_config_entry_without_type_or_class_raises() -> None:
    with pytest.raises(ValueError, match="needs a 'type' or 'class' key"):
        load_config({"transports": [{"options": {}}]})


def test_logger_from_file_json(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text('{"name": "app", "level": "ERROR"}', encoding="utf-8")

    logger = logger_from_file(config_path)

    assert logger.name == "app"
    assert logger.level == Level.ERROR


def test_logger_from_file_yaml(tmp_path: Path) -> None:
    pytest.importorskip("yaml")

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "name: app\nlevel: DEBUG\ntransports:\n  - type: console\n", encoding="utf-8"
    )

    logger = logger_from_file(config_path)

    assert logger.name == "app"
    assert logger.level == Level.DEBUG
    assert len(logger.transports) == 1


def test_logger_from_file_yaml_without_pyyaml_installed_raises_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "yaml", None)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("name: app\n", encoding="utf-8")

    with pytest.raises(ImportError, match=r"logquill\[yaml\]"):
        logger_from_file(config_path)


def test_logger_from_env_reads_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text('{"name": "app", "level": "WARN"}', encoding="utf-8")
    monkeypatch.setenv("LOGQUILL_CONFIG_FILE", str(config_path))

    logger = logger_from_env()

    assert logger.name == "app"
    assert logger.level == Level.WARN


def test_logger_from_env_level_overrides_the_config_file_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text('{"level": "WARN"}', encoding="utf-8")
    monkeypatch.setenv("LOGQUILL_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("LOGQUILL_LEVEL", "DEBUG")

    logger = logger_from_env()

    assert logger.level == Level.DEBUG


def test_logger_from_env_with_no_env_vars_set_returns_a_default_logger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOGQUILL_CONFIG_FILE", raising=False)
    monkeypatch.delenv("LOGQUILL_LEVEL", raising=False)

    logger = logger_from_env(name="fallback")

    assert logger.name == "fallback"
    assert logger.level == Level.INFO


def test_logger_from_env_custom_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOGQUILL_LEVEL", raising=False)
    monkeypatch.setenv("MYAPP_LEVEL", "ERROR")

    logger = logger_from_env(prefix="MYAPP_")

    assert logger.level == Level.ERROR


def test_transport_options_can_name_a_builtin_formatter() -> None:
    from logquill import LogfmtFormatter, TextFormatter

    logger = load_config(
        {
            "transports": [
                {"type": "console", "options": {"formatter": "text"}},
                {"type": "console", "options": {"formatter": "logfmt"}},
                {"type": "console", "options": {"formatter": "json"}},
            ]
        }
    )

    formatters = [transport.formatter for transport in logger.transports]
    assert isinstance(formatters[0], TextFormatter)
    assert isinstance(formatters[1], LogfmtFormatter)
    assert type(formatters[2]).__name__ == "JSONFormatter"


def test_an_unknown_formatter_name_lists_the_valid_ones() -> None:
    with pytest.raises(ValueError, match="Unknown formatter 'xml'.*json, logfmt, text"):
        load_config({"transports": [{"type": "console", "options": {"formatter": "xml"}}]})


def test_flush_at_exit_can_be_set_from_config() -> None:
    from logquill.runtime import shutdown

    opted_out = load_config({"flush_at_exit": False})
    default = load_config({})

    assert opted_out not in shutdown._loggers
    assert default in shutdown._loggers
