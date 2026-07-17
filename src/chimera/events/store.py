from __future__ import annotations

from collections.abc import Iterable
from threading import RLock

from chimera.events.models import Event


class InMemoryEventStore:
    """Thread-safe append-only event store."""

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._lock = RLock()

    def append(self, event: Event) -> int:
        with self._lock:
            self._events.append(event)
            return len(self._events) - 1

    def read_from(self, offset: int = 0) -> tuple[Event, ...]:
        if offset < 0:
            raise ValueError("offset must be >= 0")
        with self._lock:
            return tuple(self._events[offset:])

    def by_correlation(self, correlation_id: object) -> tuple[Event, ...]:
        with self._lock:
            return tuple(e for e in self._events if e.correlation_id == correlation_id)

    def __iter__(self) -> Iterable[Event]:
        return iter(self.read_from())

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)
