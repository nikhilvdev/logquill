from __future__ import annotations

import pytest

from logquill.core.logger import Logger
from logquill.runtime.serverless import with_azure_function, with_cloud_function, with_lambda
from logquill.transports.transport import CollectingTransport


def test_with_lambda_flushes_a_sync_handler_before_returning() -> None:
    transport = CollectingTransport()
    logger = Logger("app.test", transports=[transport], async_dispatch=True)

    @with_lambda(logger)
    def handler(event: dict, context: object) -> str:
        logger.info("handling", event=event)
        return "ok"

    result = handler({"key": "value"}, None)

    assert result == "ok"
    # Flushed synchronously before `with_lambda` returned control — no
    # sleeping/polling needed to observe the record.
    assert len(transport.records) == 1


def test_with_lambda_flushes_even_when_the_handler_raises() -> None:
    transport = CollectingTransport()
    logger = Logger("app.test", transports=[transport], async_dispatch=True)

    @with_lambda(logger)
    def handler(event: dict, context: object) -> str:
        logger.error("about to fail")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        handler({}, None)

    assert len(transport.records) == 1


async def test_with_lambda_supports_async_handlers() -> None:
    transport = CollectingTransport()
    logger = Logger("app.test", transports=[transport], async_dispatch=True)

    @with_lambda(logger)
    async def handler(event: dict, context: object) -> str:
        logger.info("handling async", event=event)
        return "ok"

    result = await handler({}, None)

    assert result == "ok"
    assert len(transport.records) == 1


def test_with_lambda_does_not_close_the_transport() -> None:
    transport = CollectingTransport()
    logger = Logger("app.test", transports=[transport], async_dispatch=True)

    @with_lambda(logger)
    def handler(event: dict, context: object) -> str:
        logger.info("invocation 1")
        return "ok"

    handler({}, None)
    handler({}, None)

    assert transport.closed is False
    assert len(transport.records) == 2


def test_with_lambda_accepts_multiple_loggers() -> None:
    app_transport = CollectingTransport()
    audit_transport = CollectingTransport()
    app_logger = Logger("app", transports=[app_transport], async_dispatch=True)
    audit_logger = Logger("audit", transports=[audit_transport], async_dispatch=True)

    @with_lambda([app_logger, audit_logger])
    def handler(event: dict, context: object) -> None:
        app_logger.info("app event")
        audit_logger.info("audit event")

    handler({}, None)

    assert len(app_transport.records) == 1
    assert len(audit_transport.records) == 1


def test_with_cloud_function_and_with_azure_function_are_the_same_behavior() -> None:
    assert with_cloud_function is with_lambda
    assert with_azure_function is with_lambda
