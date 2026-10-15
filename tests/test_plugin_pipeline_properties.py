from __future__ import annotations

from typing import Any

from adversarial import meta_dicts
from hypothesis import HealthCheck, given, settings

from logquill.core.logger import Logger
from logquill.plugins.context_plugin import ContextPlugin
from logquill.plugins.pii_redact_plugin import PIIRedactPlugin
from logquill.plugins.redact_plugin import RedactPlugin
from logquill.plugins.tamper_evident_plugin import TamperEvidentPlugin
from logquill.transports.transport import CollectingTransport


def _build_logger() -> tuple[Logger, CollectingTransport]:
    sink = CollectingTransport()
    logger = Logger(
        "app.test",
        transports=[sink],
        plugins=[
            ContextPlugin(service="api"),
            RedactPlugin(),
            PIIRedactPlugin(),
            TamperEvidentPlugin(),
        ],
    )
    return logger, sink


@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(meta=meta_dicts)
def test_pipeline_never_crashes_on_adversarial_meta(meta: dict[str, Any]) -> None:
    logger, _sink = _build_logger()

    # Must not raise, regardless of what's inside `meta` — a plugin that
    # can't handle a value fails closed (drops or leaves it untouched),
    # it never crashes the caller.
    logger.info("adversarial", **meta)


def test_pipeline_never_crashes_on_circular_reference() -> None:
    # The plugin pipeline itself handles the cycle fine (PIIRedactPlugin's
    # cycle guard, TamperEvidentPlugin's `default=str` fallback). JSON
    # genuinely can't represent a cycle, so the transport's `format()` call
    # legitimately fails here — the point of this test is that the failure
    # is caught and logged rather than propagated to the caller.
    logger, _sink = _build_logger()
    cyclic: dict[str, Any] = {"a": 1}
    cyclic["self"] = cyclic

    record = logger.info("cyclic", data=cyclic)

    assert record is not None


def test_pipeline_never_crashes_on_deeply_nested_meta() -> None:
    logger, _sink = _build_logger()
    nested: dict[str, Any] = {}
    cursor = nested
    for _ in range(500):
        cursor["child"] = {}
        cursor = cursor["child"]

    logger.info("deeply nested", data=nested)
