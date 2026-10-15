from logquill.core.logger import Logger
from logquill.plugins.context_plugin import ContextPlugin


def test_injects_fixed_context_into_meta() -> None:
    logger = Logger("app.test", plugins=[ContextPlugin(service="api", env="prod")])

    record = logger.info("hello", user_id=42)

    assert record is not None
    assert record["meta"] == {"service": "api", "env": "prod", "user_id": 42}


def test_call_site_meta_overrides_fixed_context() -> None:
    logger = Logger("app.test", plugins=[ContextPlugin(env="prod")])

    record = logger.info("hello", env="staging")

    assert record is not None
    assert record["meta"]["env"] == "staging"
