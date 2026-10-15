from __future__ import annotations

from typing import Any, Protocol, Sequence, cast

from logquill.core.records import LogRecord
from logquill.formatters import Formatter
from logquill.transports.batching_transport import BatchingTransport

_SEVERITY = {
    "TRACE": "DEBUG",
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "WARN": "WARNING",
    "ERROR": "ERROR",
    "FATAL": "CRITICAL",
}


class CloudLoggingClientLike(Protocol):
    """The subset of `google-cloud-logging`'s `Logger` client this transport
    calls — implement this shape to inject a fake in tests without
    installing the real driver."""

    def log_struct(self, info: dict[str, Any], severity: str) -> None:
        """Write one structured log entry at the given severity."""
        ...


class CloudLoggingTransport(BatchingTransport[LogRecord]):
    """Ships records to Google Cloud Logging via `log_struct`, through the
    optional `google-cloud-logging` peer dependency, mapping `level` onto
    Cloud Logging's `severity` scale. One `log_struct` call per record —
    the client library has no native multi-record batch form at this
    level. Pass `client` to inject a pre-built Cloud Logging `Logger` (or
    a fake, for tests), or `log_name` to let this transport connect
    itself."""

    def __init__(
        self,
        *,
        log_name: str = "logquill",
        client: CloudLoggingClientLike | None = None,
        formatter: Formatter | None = None,
        max_records: int = 100,
        max_bytes: int = 1_000_000,
    ) -> None:
        """`log_name` names the Cloud Logging log this transport writes to
        when it connects its own client (ignored if `client` is given)."""
        super().__init__(formatter=formatter, max_records=max_records, max_bytes=max_bytes)
        self._log_name = log_name
        self._injected = client
        self._client: CloudLoggingClientLike | None = None

    def _resolved_client(self) -> CloudLoggingClientLike:
        if self._injected is not None:
            return self._injected
        if self._client is None:
            self._client = self._import_client()
        return self._client

    def _import_client(self) -> CloudLoggingClientLike:
        try:
            import google.cloud.logging as cloud_logging  # type: ignore  # stubs vary by env
        except ImportError as exc:
            raise ImportError(
                "CloudLoggingTransport: install `google-cloud-logging` to "
                "use this transport without providing a client — "
                "`pip install logquill[gcp-logging]`"
            ) from exc
        return cast(CloudLoggingClientLike, cloud_logging.Client().logger(self._log_name))

    def _send_batch(self, batch: Sequence[LogRecord]) -> None:
        client = self._resolved_client()
        for record in batch:
            client.log_struct(dict(record), severity=_SEVERITY.get(record["level"], "DEFAULT"))
