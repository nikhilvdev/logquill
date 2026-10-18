from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from logquill.adapters.base import LogQuillAdapter
from logquill.config import load_config, logger_from_env, logger_from_file
from logquill.core.context import bind_context, current_context
from logquill.core.exceptions import format_exc_info
from logquill.core.handler import LogQuillHandler
from logquill.core.levels import Level, parse_level
from logquill.core.logger import Logger
from logquill.core.opt import OptLogger
from logquill.core.records import SCHEMA_VERSION, LLMBlock, LogRecord, parse_record
from logquill.formatters import Formatter, JSONFormatter, LogfmtFormatter, TextFormatter
from logquill.parsing import TEXT_LOG_CASTS, TEXT_LOG_PATTERN, parse, parse_logfmt
from logquill.plugins.adaptive_sampling_plugin import AdaptiveSamplingPlugin
from logquill.plugins.alerting_plugin import AlertingPlugin
from logquill.plugins.context_plugin import ContextPlugin
from logquill.plugins.flight_recorder_plugin import FlightRecorderPlugin
from logquill.plugins.metrics_plugin import MetricsBackend, MetricsPlugin
from logquill.plugins.pii_redact_plugin import PIIRedactPlugin
from logquill.plugins.plugin import FunctionPlugin, Plugin
from logquill.plugins.rate_limit_plugin import RateLimitPlugin
from logquill.plugins.redact_plugin import RedactPlugin
from logquill.plugins.run_plugin import RunPlugin
from logquill.plugins.run_summary_plugin import RunSummaryPlugin
from logquill.plugins.sampling_plugin import SamplingPlugin
from logquill.plugins.tamper_evident_plugin import (
    TamperEvidentPlugin,
    VerificationResult,
    sign_head,
    verify_chain_detailed,
    verify_head_signature,
    verify_signed_chain,
)
from logquill.plugins.trace_context_plugin import TraceContextPlugin
from logquill.privacy.audit import AuditLogger
from logquill.privacy.privacy import CONTENT_FIELDS, FIELD_CLASSES, ContentCapturePolicy, FieldClass
from logquill.runtime.runtime_level import (
    LevelEnvWatcher,
    LevelFileWatcher,
    install_signal_level_handler,
)
from logquill.runtime.serverless import with_azure_function, with_cloud_function, with_lambda
from logquill.runtime.toggle import disable, enable, is_enabled
from logquill.runtime.worker import AsyncWorker
from logquill.transports.batching_transport import BatchingTransport
from logquill.transports.console_transport import ConsoleTransport
from logquill.transports.file_transport import FileTransport
from logquill.transports.transport import CollectingTransport, Transport

if TYPE_CHECKING:  # for type checkers and doc tools; the real import happens on first use
    from logquill.plugins.apprise_alert_plugin import AppriseAlertPlugin
    from logquill.plugins.email_alert_plugin import EmailAlertPlugin
    from logquill.plugins.otel_metrics_backend import OTelMetricsBackend
    from logquill.plugins.pagerduty_alert_plugin import PagerDutyAlertPlugin
    from logquill.plugins.prometheus_pushgateway_backend import PrometheusPushgatewayBackend
    from logquill.plugins.slack_alert_plugin import SlackAlertPlugin
    from logquill.plugins.statsd_metrics_backend import StatsDBackend
    from logquill.transports.cloud.app_insights_transport import AppInsightsTransport
    from logquill.transports.cloud.cloud_logging_transport import CloudLoggingTransport
    from logquill.transports.cloud.cloudwatch_transport import CloudWatchTransport
    from logquill.transports.cloud.datadog_transport import DatadogTransport
    from logquill.transports.cloud.elasticsearch_transport import ElasticsearchTransport
    from logquill.transports.cloud.new_relic_transport import NewRelicTransport
    from logquill.transports.cloud.syslog_transport import SyslogTransport
    from logquill.transports.graph.base_graph_transport import BaseGraphTransport, GraphRow
    from logquill.transports.graph.falkordb_transport import FalkorDBTransport
    from logquill.transports.graph.neo4j_transport import Neo4jTransport
    from logquill.transports.graph.neptune_transport import NeptuneTransport
    from logquill.transports.http_transport import HTTPTransport
    from logquill.transports.nosql.dynamodb_transport import DynamoDBTransport
    from logquill.transports.nosql.mongodb_transport import MongoDBTransport
    from logquill.transports.nosql.redis_transport import RedisTransport
    from logquill.transports.otel.logs_transport import OTelLogsTransport
    from logquill.transports.otel.otlp_transport import OTLPTransport
    from logquill.transports.queue.base_queue_transport import BaseQueueTransport
    from logquill.transports.queue.kafka_transport import KafkaTransport
    from logquill.transports.queue.pubsub_transport import PubSubTransport
    from logquill.transports.queue.rabbitmq_transport import RabbitMQTransport
    from logquill.transports.queue.sqs_transport import SQSTransport
    from logquill.transports.sql.base_sql_transport import BaseSQLTransport, SQLLogRow
    from logquill.transports.sql.mysql_transport import MySQLTransport
    from logquill.transports.sql.postgres_transport import PostgresTransport
    from logquill.transports.sql.sqlite_transport import SQLiteTransport

__version__ = "2.0.0"

__all__ = [
    "AdaptiveSamplingPlugin",
    "AlertingPlugin",
    "AppriseAlertPlugin",
    "AppInsightsTransport",
    "AsyncWorker",
    "AuditLogger",
    "BaseGraphTransport",
    "BaseQueueTransport",
    "BaseSQLTransport",
    "BatchingTransport",
    "CloudLoggingTransport",
    "CloudWatchTransport",
    "CollectingTransport",
    "CONTENT_FIELDS",
    "ConsoleTransport",
    "ContentCapturePolicy",
    "ContextPlugin",
    "DatadogTransport",
    "DynamoDBTransport",
    "ElasticsearchTransport",
    "EmailAlertPlugin",
    "FIELD_CLASSES",
    "FalkorDBTransport",
    "FieldClass",
    "FileTransport",
    "FlightRecorderPlugin",
    "Formatter",
    "FunctionPlugin",
    "GraphRow",
    "HTTPTransport",
    "JSONFormatter",
    "KafkaTransport",
    "Level",
    "LevelEnvWatcher",
    "LevelFileWatcher",
    "LogfmtFormatter",
    "LLMBlock",
    "LogQuillAdapter",
    "LogQuillHandler",
    "LogRecord",
    "Logger",
    "MetricsBackend",
    "MetricsPlugin",
    "MongoDBTransport",
    "MySQLTransport",
    "Neo4jTransport",
    "NeptuneTransport",
    "NewRelicTransport",
    "OTLPTransport",
    "OTelLogsTransport",
    "OTelMetricsBackend",
    "OptLogger",
    "PIIRedactPlugin",
    "PagerDutyAlertPlugin",
    "Plugin",
    "PostgresTransport",
    "PrometheusPushgatewayBackend",
    "PubSubTransport",
    "RabbitMQTransport",
    "RateLimitPlugin",
    "RedactPlugin",
    "RedisTransport",
    "RunPlugin",
    "RunSummaryPlugin",
    "SCHEMA_VERSION",
    "SQLLogRow",
    "SQLiteTransport",
    "SQSTransport",
    "SamplingPlugin",
    "SlackAlertPlugin",
    "StatsDBackend",
    "SyslogTransport",
    "TEXT_LOG_CASTS",
    "TEXT_LOG_PATTERN",
    "TamperEvidentPlugin",
    "TextFormatter",
    "TraceContextPlugin",
    "Transport",
    "VerificationResult",
    "bind_context",
    "current_context",
    "disable",
    "enable",
    "format_exc_info",
    "install_signal_level_handler",
    "is_enabled",
    "load_config",
    "logger_from_env",
    "logger_from_file",
    "parse",
    "parse_level",
    "parse_logfmt",
    "parse_record",
    "sign_head",
    "verify_chain_detailed",
    "verify_head_signature",
    "verify_signed_chain",
    "with_azure_function",
    "with_cloud_function",
    "with_lambda",
    "__version__",
]

# Names that are imported the first time they are used, not on `import logquill`:
# these classes pull in stdlib modules (`urllib`, `ssl`, `smtplib`, `socket`,
# `sqlite3`) or optional-dependency wrappers most programs never touch, and
# together they were about half of the package's import time.
_LAZY_EXPORTS: dict[str, str] = {
    "AppInsightsTransport": "logquill.transports.cloud.app_insights_transport",
    "AppriseAlertPlugin": "logquill.plugins.apprise_alert_plugin",
    "BaseGraphTransport": "logquill.transports.graph.base_graph_transport",
    "BaseQueueTransport": "logquill.transports.queue.base_queue_transport",
    "BaseSQLTransport": "logquill.transports.sql.base_sql_transport",
    "CloudLoggingTransport": "logquill.transports.cloud.cloud_logging_transport",
    "CloudWatchTransport": "logquill.transports.cloud.cloudwatch_transport",
    "DatadogTransport": "logquill.transports.cloud.datadog_transport",
    "DynamoDBTransport": "logquill.transports.nosql.dynamodb_transport",
    "ElasticsearchTransport": "logquill.transports.cloud.elasticsearch_transport",
    "EmailAlertPlugin": "logquill.plugins.email_alert_plugin",
    "FalkorDBTransport": "logquill.transports.graph.falkordb_transport",
    "GraphRow": "logquill.transports.graph.base_graph_transport",
    "HTTPTransport": "logquill.transports.http_transport",
    "KafkaTransport": "logquill.transports.queue.kafka_transport",
    "MongoDBTransport": "logquill.transports.nosql.mongodb_transport",
    "MySQLTransport": "logquill.transports.sql.mysql_transport",
    "Neo4jTransport": "logquill.transports.graph.neo4j_transport",
    "NeptuneTransport": "logquill.transports.graph.neptune_transport",
    "NewRelicTransport": "logquill.transports.cloud.new_relic_transport",
    "OTLPTransport": "logquill.transports.otel.otlp_transport",
    "OTelLogsTransport": "logquill.transports.otel.logs_transport",
    "OTelMetricsBackend": "logquill.plugins.otel_metrics_backend",
    "PagerDutyAlertPlugin": "logquill.plugins.pagerduty_alert_plugin",
    "PostgresTransport": "logquill.transports.sql.postgres_transport",
    "PrometheusPushgatewayBackend": "logquill.plugins.prometheus_pushgateway_backend",
    "PubSubTransport": "logquill.transports.queue.pubsub_transport",
    "RabbitMQTransport": "logquill.transports.queue.rabbitmq_transport",
    "RedisTransport": "logquill.transports.nosql.redis_transport",
    "SQLLogRow": "logquill.transports.sql.base_sql_transport",
    "SQLiteTransport": "logquill.transports.sql.sqlite_transport",
    "SQSTransport": "logquill.transports.queue.sqs_transport",
    "SlackAlertPlugin": "logquill.plugins.slack_alert_plugin",
    "StatsDBackend": "logquill.plugins.statsd_metrics_backend",
    "SyslogTransport": "logquill.transports.cloud.syslog_transport",
}


def __getattr__(name: str) -> Any:
    """Import a lazily-exported name on first access (PEP 562) and cache it."""
    module = _LAZY_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
