from logquill.core.logger import Logger
from logquill.plugins.redact_plugin import RedactPlugin


def test_redacts_default_sensitive_keys() -> None:
    logger = Logger("app.test", plugins=[RedactPlugin()])

    record = logger.info("login", password="hunter2", user_id=42)

    assert record is not None
    assert record["meta"]["password"] == "***"
    assert record["meta"]["user_id"] == 42


def test_matches_keys_case_insensitively() -> None:
    logger = Logger("app.test", plugins=[RedactPlugin()])

    record = logger.info("login", Password="hunter2")

    assert record is not None
    assert record["meta"]["Password"] == "***"


def test_custom_keys_and_replacement() -> None:
    logger = Logger("app.test", plugins=[RedactPlugin(keys=["ssn"], replacement="[REDACTED]")])

    record = logger.info("submit", ssn="123-45-6789", password="not redacted here")

    assert record is not None
    assert record["meta"]["ssn"] == "[REDACTED]"
    assert record["meta"]["password"] == "not redacted here"


def test_classes_adds_every_key_tagged_with_that_class() -> None:
    from logquill.privacy.privacy import keys_in_class

    plugin = RedactPlugin(keys=(), classes=["secret"])

    assert plugin.keys == {key.lower() for key in keys_in_class("secret")}


def test_classes_combines_with_explicit_keys() -> None:
    logger = Logger("app.test", plugins=[RedactPlugin(keys=["my_custom_key"], classes=["secret"])])

    record = logger.info("x", my_custom_key="x", password="y", other="z")

    assert record is not None
    assert record["meta"] == {"my_custom_key": "***", "password": "***", "other": "z"}


def test_content_classed_redaction_strips_prompt_fields_too() -> None:
    # content_policy="full": isolates RedactPlugin's own classes=["content"]
    # redaction from Logger's own content-capture policy, which would
    # otherwise mask meta.input_messages first regardless of this plugin.
    logger = Logger(
        "app.test",
        plugins=[RedactPlugin(keys=(), classes=["content"])],
        content_policy="full",
    )

    record = logger.info("x", input_messages=["raw"], other=1)

    assert record is not None
    assert record["meta"] == {"input_messages": "***", "other": 1}


def test_default_redacted_keys_still_matches_the_original_fixed_set() -> None:
    from logquill.plugins.redact_plugin import DEFAULT_REDACTED_KEYS

    assert (
        frozenset({"password", "token", "secret", "api_key", "authorization"})
        == DEFAULT_REDACTED_KEYS
    )
