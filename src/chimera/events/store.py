from __future__ import annotations

from collections.abc import Iterable, Iterator
from threading import RLock
from typing import Protocol

from chimera.events.models import Event


class EventStore(Protocol):
    """Append-only event storage with stable, zero-based offsets."""

    def append(self, event: Event) -> int:
        """Durably append one event and return its zero-based offset."""

    def append_many(self, events: Iterable[Event]) -> tuple[int, ...]:
        """Atomically append events and return their zero-based offsets."""

    def read_from(self, offset: int = 0) -> tuple[Event, ...]:
        """Return the ordered event suffix beginning at ``offset``."""

    def by_correlation(self, correlation_id: object) -> tuple[Event, ...]:
        """Return events with the supplied correlation identifier, in order."""

    def __iter__(self) -> Iterator[Event]:
        """Return a snapshot iterator over all events in offset order."""

    def __len__(self) -> int:
        """Return the number of events currently stored."""


class InMemoryEventStore:
    """Thread-safe append-only event store with atomic batch appends."""

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._event_ids: set[object] = set()
        self._lock = RLock()

    def append(self, event: Event) -> int:
        """Append one event and return its zero-based offset."""
        return self.append_many((event,))[0]

    def append_many(self, events: Iterable[Event]) -> tuple[int, ...]:
        """Atomically append ``events`` or leave the store unchanged."""
        batch = tuple(event.snapshot() for event in events)
        with self._lock:
            batch_ids: set[object] = set()
            for event in batch:
                if event.event_id in self._event_ids or event.event_id in batch_ids:
                    raise ValueError(f"duplicate event_id: {event.event_id}")
                batch_ids.add(event.event_id)

            first_offset = len(self._events)
            self._events.extend(batch)
            self._event_ids.update(batch_ids)
            return tuple(range(first_offset, first_offset + len(batch)))

    def read_from(self, offset: int = 0) -> tuple[Event, ...]:
        """Return the ordered event suffix beginning at ``offset``."""
        if offset < 0:
            raise ValueError("offset must be >= 0")
        with self._lock:
            return tuple(event.snapshot() for event in self._events[offset:])

    def by_correlation(self, correlation_id: object) -> tuple[Event, ...]:
        """Return events sharing ``correlation_id`` in append order."""
        with self._lock:
            return tuple(
                e.snapshot() for e in self._events if e.correlation_id == correlation_id
            )

    def __iter__(self) -> Iterator[Event]:
        return iter(self.read_from())

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)
