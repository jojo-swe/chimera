from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import TypeAlias
from uuid import UUID

from chimera.events.models import Event

JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


@dataclass(frozen=True, slots=True)
class _SerializedEvent:
    event_id: str
    event_type: str
    actor_id: str
    payload: str
    correlation_id: str
    causation_id: str | None
    occurred_at: str


class SQLiteEventStore:
    """A durable, append-only SQLite implementation of the event store protocol."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = RLock()
        self._closed = False
        self._connection = sqlite3.connect(
            str(self._path),
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        try:
            self._configure_connection()
            self._create_schema()
        except Exception:
            self._connection.close()
            self._closed = True
            raise

    def __enter__(self) -> SQLiteEventStore:
        with self._lock:
            self._require_open()
            return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying database connection.

        Operations after closing raise ``RuntimeError`` rather than leaking a
        driver-specific programming error.
        """
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def append(self, event: Event) -> int:
        """Durably append one event and return its zero-based offset."""
        return self.append_many((event,))[0]

    def append_many(self, events: Iterable[Event]) -> tuple[int, ...]:
        """Atomically serialize and durably append an event batch.

        The full batch is serialized and checked for internal duplicates before
        opening a write transaction, so any serialization failure leaves the
        database untouched.
        """
        with self._lock:
            self._require_open()
        batch = tuple(self._serialize_event(event) for event in events)
        self._ensure_unique_batch_event_ids(batch)
        if not batch:
            return ()

        with self._lock:
            self._require_open()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                for event in batch:
                    exists = self._connection.execute(
                        "SELECT 1 FROM events WHERE event_id = ?", (event.event_id,)
                    ).fetchone()
                    if exists is not None:
                        raise ValueError(f"duplicate event_id: {event.event_id}")

                row = self._connection.execute(
                    "SELECT COALESCE(MAX(event_offset), -1) + 1 AS next_offset FROM events"
                ).fetchone()
                if row is None:
                    raise RuntimeError("could not allocate an event offset")
                first_offset = int(row["next_offset"])

                for index, event in enumerate(batch):
                    self._connection.execute(
                        """
                        INSERT INTO events (
                            event_offset, event_id, event_type, actor_id, payload,
                            correlation_id, causation_id, occurred_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            first_offset + index,
                            event.event_id,
                            event.event_type,
                            event.actor_id,
                            event.payload,
                            event.correlation_id,
                            event.causation_id,
                            event.occurred_at,
                        ),
                    )
                self._connection.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                self._rollback()
                if "event_id" in str(exc):
                    raise ValueError("duplicate event_id") from exc
                raise
            except BaseException:
                self._rollback()
                raise

            return tuple(range(first_offset, first_offset + len(batch)))

    def read_from(self, offset: int = 0) -> tuple[Event, ...]:
        """Return events from ``offset`` onward in stable offset order."""
        if offset < 0:
            raise ValueError("offset must be >= 0")
        with self._lock:
            self._require_open()
            rows = self._connection.execute(
                """
                SELECT event_id, event_type, actor_id, payload, correlation_id,
                       causation_id, occurred_at
                FROM events
                WHERE event_offset >= ?
                ORDER BY event_offset ASC
                """,
                (offset,),
            ).fetchall()
        return tuple(self._deserialize_event(row) for row in rows)

    def by_correlation(self, correlation_id: object) -> tuple[Event, ...]:
        """Return events sharing ``correlation_id`` in stable offset order."""
        with self._lock:
            self._require_open()
            rows = self._connection.execute(
                """
                SELECT event_id, event_type, actor_id, payload, correlation_id,
                       causation_id, occurred_at
                FROM events
                WHERE correlation_id = ?
                ORDER BY event_offset ASC
                """,
                (str(correlation_id),),
            ).fetchall()
        return tuple(self._deserialize_event(row) for row in rows)

    def __iter__(self) -> Iterator[Event]:
        return iter(self.read_from())

    def __len__(self) -> int:
        with self._lock:
            self._require_open()
            row = self._connection.execute("SELECT COUNT(*) AS event_count FROM events").fetchone()
            if row is None:
                raise RuntimeError("could not count events")
            return int(row["event_count"])

    def _configure_connection(self) -> None:
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._connection.execute("PRAGMA journal_mode = WAL").fetchone()
        self._connection.execute("PRAGMA synchronous = FULL")

    def _create_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_offset INTEGER PRIMARY KEY CHECK (event_offset >= 0),
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                causation_id TEXT,
                occurred_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS events_correlation_offset_idx
            ON events (correlation_id, event_offset)
            """
        )
        self._connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS events_no_update
            BEFORE UPDATE ON events
            BEGIN
                SELECT RAISE(ABORT, 'events are append-only: updates are forbidden');
            END
            """
        )
        self._connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS events_no_delete
            BEFORE DELETE ON events
            BEGIN
                SELECT RAISE(ABORT, 'events are append-only: deletes are forbidden');
            END
            """
        )
        self._connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS events_no_replace
            BEFORE INSERT ON events
            WHEN EXISTS (
                SELECT 1 FROM events
                WHERE event_offset = NEW.event_offset OR event_id = NEW.event_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'events are append-only: replacements are forbidden');
            END
            """
        )

    def _rollback(self) -> None:
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("SQLiteEventStore is closed")

    @staticmethod
    def _ensure_unique_batch_event_ids(batch: tuple[_SerializedEvent, ...]) -> None:
        event_ids: set[str] = set()
        for event in batch:
            if event.event_id in event_ids:
                raise ValueError(f"duplicate event_id: {event.event_id}")
            event_ids.add(event.event_id)

    @classmethod
    def _serialize_event(cls, event: Event) -> _SerializedEvent:
        payload_value = cls._validate_json_value(event.payload, "payload")
        if not isinstance(payload_value, dict):
            raise TypeError("payload must be a mapping with string keys")
        return _SerializedEvent(
            event_id=str(event.event_id),
            event_type=event.event_type,
            actor_id=event.actor_id,
            payload=json.dumps(
                payload_value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            correlation_id=str(event.correlation_id),
            causation_id=str(event.causation_id) if event.causation_id is not None else None,
            occurred_at=event.occurred_at.isoformat(),
        )

    @staticmethod
    def _validate_json_value(value: object, path: str) -> JSONValue:
        if value is None or isinstance(value, (bool, int, str)):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError(f"{path} contains a non-finite float")
            return value
        if isinstance(value, Mapping):
            converted: dict[str, JSONValue] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError(f"{path} has a non-string key of type {type(key).__name__}")
                converted[key] = SQLiteEventStore._validate_json_value(item, f"{path}[{key!r}]")
            return converted
        if isinstance(value, list):
            return [
                SQLiteEventStore._validate_json_value(item, f"{path}[{index}]")
                for index, item in enumerate(value)
            ]
        raise TypeError(f"{path} contains unsupported value of type {type(value).__name__}")

    @staticmethod
    def _deserialize_event(row: sqlite3.Row) -> Event:
        payload = json.loads(str(row["payload"]))
        if not isinstance(payload, dict):
            raise ValueError("stored event payload must be a JSON object")
        return Event(
            event_type=str(row["event_type"]),
            actor_id=str(row["actor_id"]),
            payload=payload,
            correlation_id=UUID(str(row["correlation_id"])),
            causation_id=(
                UUID(str(row["causation_id"])) if row["causation_id"] is not None else None
            ),
            event_id=UUID(str(row["event_id"])),
            occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
        )
