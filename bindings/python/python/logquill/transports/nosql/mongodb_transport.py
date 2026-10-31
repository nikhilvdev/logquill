from __future__ import annotations

from typing import Any, Protocol, Sequence, cast

from logquill.core.records import LogRecord
from logquill.formatters import Formatter
from logquill.transports.batching_transport import BatchingTransport


class MongoCollectionLike(Protocol):
    """The subset of `pymongo`'s `Collection` this transport calls —
    implement this shape to inject a fake in tests without installing the
    real driver."""

    def insert_many(self, documents: Sequence[dict[str, Any]]) -> object:
        """Insert a batch of documents in one call."""
        ...


class MongoClientLike(Protocol):
    """The subset of `pymongo`'s `MongoClient` this transport calls to
    release its connection on shutdown — implement this shape to inject a
    fake in tests without installing the real driver."""

    def close(self) -> None:
        """Release the client's connection resources."""
        ...


class MongoDBTransport(BatchingTransport[LogRecord]):
    """Batches log records into `insert_many()` calls against a MongoDB
    collection — records map 1:1 to documents, no JSON-in-a-column
    workaround needed. Pass `collection` to inject a pre-built
    `pymongo.Collection` (or a fake, for tests), or `uri`/`database`/
    `collection_name` to let this transport connect itself via the
    optional `pymongo` peer dependency."""

    def __init__(
        self,
        *,
        collection: MongoCollectionLike | None = None,
        uri: str = "mongodb://localhost:27017",
        database: str = "logquill",
        collection_name: str = "logs",
        formatter: Formatter | None = None,
        max_records: int = 100,
        max_bytes: int = 1_000_000,
    ) -> None:
        """`uri`/`database`/`collection_name` are used only when this
        transport connects its own `pymongo` client (ignored if
        `collection` is given)."""
        super().__init__(formatter=formatter, max_records=max_records, max_bytes=max_bytes)
        self._injected = collection
        self._uri = uri
        self._database = database
        self._collection_name = collection_name
        self._collection: MongoCollectionLike | None = None
        self._client: MongoClientLike | None = None

    def _resolved_collection(self) -> MongoCollectionLike:
        if self._injected is not None:
            return self._injected
        if self._collection is None:
            self._collection = self._import_collection()
        return self._collection

    def _import_collection(self) -> MongoCollectionLike:
        try:
            import pymongo  # type: ignore  # stub availability for this optional dep varies by env
        except ImportError as exc:
            raise ImportError(
                "MongoDBTransport: install `pymongo` to use this transport "
                "without providing a collection — `pip install logquill[mongodb]`"
            ) from exc
        client = pymongo.MongoClient(self._uri)
        self._client = cast(MongoClientLike, client)
        return cast(MongoCollectionLike, client[self._database][self._collection_name])

    def close(self) -> None:
        """Flushes any remaining buffered records, then closes the
        self-connected client — never a client passed in as `collection`,
        since this transport doesn't own that connection's lifecycle."""
        super().close()
        if self._injected is None and self._client is not None:
            self._client.close()

    def _send_batch(self, batch: Sequence[LogRecord]) -> None:
        collection = self._resolved_collection()
        collection.insert_many([dict(record) for record in batch])
