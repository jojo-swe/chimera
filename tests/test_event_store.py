from __future__ import annotations

import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from math import inf, nan
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

import pytest

from chimera.events import Event, EventStore, InMemoryEventStore, SQLiteEventStore


class StoreFactory(Protocol):
    def __call__(self) -> EventStore: ...


def make_event(
    event_type: str,
    *,
    correlation_id: UUID | None = None,
    event_id: UUID | None = None,
    payload: dict[str, object] | None = None,
) -> Event:
    return Event(
        event_type=event_type,
        actor_id="tester",
        correlation_id=correlation_id or uuid4(),
        event_id=event_id or uuid4(),
        payload=payload or {},
    )


@pytest.fixture(params=["memory", "sqlite"])
def store_factory(request: pytest.FixtureRequest, tmp_path: Path) -> StoreFactory:
    if request.param == "memory":
        return InMemoryEventStore
    database_path = tmp_path / "events.sqlite3"
    return lambda: SQLiteEventStore(database_path)


def close_store(store: EventStore) -> None:
    if isinstance(store, SQLiteEventStore):
        store.close()


def test_store_parity_ordering_offsets_filtering_and_duplicate_atomicity(
    store_factory: StoreFactory,
) -> None:
    store = store_factory()
    try:
        correlation = uuid4()
        first = make_event("task.created", correlation_id=correlation)
        second = make_event("task.started", correlation_id=correlation)
        unrelated = make_event("task.completed")

        assert store.append(first) == 0
        assert store.append_many((second, unrelated)) == (1, 2)
        assert store.read_from() == (first, second, unrelated)
        assert store.read_from(1) == (second, unrelated)
        assert tuple(store) == (first, second, unrelated)
        assert store.by_correlation(correlation) == (first, second)
        assert len(store) == 3
        with pytest.raises(ValueError, match="duplicate event_id"):
            store.append(first)

        duplicate_in_batch = make_event("task.failed", event_id=second.event_id)
        with pytest.raises(ValueError, match="duplicate event_id"):
            store.append_many((make_event("task.queued"), duplicate_in_batch))
        assert store.read_from() == (first, second, unrelated)
        with pytest.raises(ValueError, match="offset"):
            store.read_from(-1)
    finally:
        close_store(store)


def test_sqlite_round_trips_uuids_timezone_unicode_and_nested_json(tmp_path: Path) -> None:
    database_path = tmp_path / "events.sqlite3"
    correlation_id = uuid4()
    causation_id = uuid4()
    event_id = uuid4()
    occurred_at = datetime(
        2025,
        1,
        2,
        3,
        4,
        5,
        123456,
        tzinfo=timezone(timedelta(hours=5, minutes=30)),
    )
    payload: dict[str, object] = {
        "greeting": "こんにちは, Chimera — café",
        "nested": {"items": [None, True, 4, 1.25, {"name": "München"}]},
    }
    event = Event(
        event_type="task.created",
        actor_id="人間",
        payload=payload,
        correlation_id=correlation_id,
        causation_id=causation_id,
        event_id=event_id,
        occurred_at=occurred_at,
    )

    with SQLiteEventStore(database_path) as store:
        assert store.append(event) == 0
        restored = store.read_from()[0]

    assert restored.event_id == event_id
    assert restored.correlation_id == correlation_id
    assert restored.causation_id == causation_id
    assert restored.occurred_at == occurred_at
    assert restored.event_type == event.event_type
    assert restored.actor_id == event.actor_id
    assert restored.payload == payload


def test_sqlite_close_and_reopen_preserves_events(tmp_path: Path) -> None:
    database_path = tmp_path / "events.sqlite3"
    event = make_event("task.created")
    store = SQLiteEventStore(database_path)
    assert store.append(event) == 0
    store.close()

    with pytest.raises(RuntimeError, match="closed"):
        store.read_from()

    with SQLiteEventStore(database_path) as reopened:
        assert reopened.read_from() == (event,)
        assert reopened.append(make_event("task.started")) == 1


def test_sqlite_context_manager_closes_store(tmp_path: Path) -> None:
    with SQLiteEventStore(tmp_path / "events.sqlite3") as store:
        store.append(make_event("task.created"))
    with pytest.raises(RuntimeError, match="closed"):
        len(store)
    with pytest.raises(RuntimeError, match="closed"):
        store.append_many(())


def test_sqlite_triggers_refuse_row_update_and_delete(tmp_path: Path) -> None:
    with SQLiteEventStore(tmp_path / "events.sqlite3") as store:
        event = make_event("task.created")
        store.append(event)
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._connection.execute("UPDATE events SET actor_id = ?", ("other",))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._connection.execute("DELETE FROM events")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store._connection.execute("""
                INSERT OR REPLACE INTO events
                SELECT event_offset, event_id, event_type, 'other', payload,
                       correlation_id, causation_id, occurred_at FROM events
            """)
        assert store.read_from() == (event,)


@pytest.mark.parametrize(
    "payload, exception",
    [
        ({"tuple": ("not", "json")}, TypeError),
        ({"set": {"not", "json"}}, TypeError),
        ({"bad": {1: "non-string key"}}, TypeError),
        ({"nan": nan}, ValueError),
        ({"infinity": inf}, ValueError),
        ({"uuid": uuid4()}, TypeError),
    ],
)
def test_sqlite_rejects_non_json_payloads_without_partial_batch_mutation(
    tmp_path: Path,
    payload: dict[str, object],
    exception: type[Exception],
) -> None:
    with SQLiteEventStore(tmp_path / "events.sqlite3") as store:
        existing = make_event("task.created")
        valid = make_event("task.started")
        invalid = make_event("task.failed", payload=payload)
        store.append(existing)

        with pytest.raises(exception, match="payload"):
            store.append_many((valid, invalid))

        assert store.read_from() == (existing,)


def test_sqlite_two_connections_threaded_appends_have_unique_ordered_offsets(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "events.sqlite3"
    first_store = SQLiteEventStore(database_path)
    second_store = SQLiteEventStore(database_path)
    events = [make_event(f"task.step_{index}") for index in range(48)]

    try:
        def append_at(index: int) -> int:
            store = first_store if index % 2 == 0 else second_store
            return store.append(events[index])

        with ThreadPoolExecutor(max_workers=12) as executor:
            offsets = list(executor.map(append_at, range(len(events))))

        assert sorted(offsets) == list(range(len(events)))
        replayed = first_store.read_from()
        assert len(replayed) == len(events)
        assert {event.event_id for event in replayed} == {event.event_id for event in events}
        expected = tuple(event for _, event in sorted(zip(offsets, events, strict=True)))
        assert replayed == expected
    finally:
        first_store.close()
        second_store.close()


def test_event_store_protocol_is_implemented(tmp_path: Path) -> None:
    stores: tuple[EventStore, ...] = (
        InMemoryEventStore(),
        SQLiteEventStore(tmp_path / "events.sqlite3"),
    )
    try:
        for store in stores:
            assert store.append_many(()) == ()
    finally:
        for store in stores:
            close_store(store)


def test_duplicate_inside_batch_never_appends_any_event(store_factory: StoreFactory) -> None:
    store = store_factory()
    try:
        event = make_event("task.created")
        with pytest.raises(ValueError, match="duplicate event_id"):
            store.append_many((event, event))
        assert len(store) == 0
        assert store.append(event) == 0
    finally:
        close_store(store)


def test_sql_failure_mid_batch_rolls_back_and_preserves_next_offset(tmp_path: Path) -> None:
    with SQLiteEventStore(tmp_path / "events.db") as store:
        existing = make_event("task.created")
        store.append(existing)
        store._connection.execute("""
            CREATE TRIGGER reject_blocked BEFORE INSERT ON events
            WHEN NEW.actor_id = 'blocked'
            BEGIN SELECT RAISE(ABORT, 'injected write failure'); END
        """)
        valid = make_event("task.started")
        blocked = Event(event_type="task.started", actor_id="blocked")
        with pytest.raises(sqlite3.IntegrityError, match="injected write failure"):
            store.append_many((valid, blocked))
        assert store.read_from() == (existing,)
        assert store.append(valid) == 1


def test_process_death_mid_transaction_loses_no_committed_history(tmp_path: Path) -> None:
    path = tmp_path / "events.db"
    existing = make_event("task.created")
    with SQLiteEventStore(path) as store:
        store.append(existing)
    code = '''
import os, sqlite3, sys
from chimera.events import Event, SQLiteEventStore
connect = sqlite3.connect
class CrashConnection(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        result = super().execute(sql, parameters)
        if sql.lstrip().startswith("INSERT INTO events"):
            os._exit(42)
        return result
sqlite3.connect = lambda *args, **kwargs: connect(*args, **kwargs, factory=CrashConnection)
store = SQLiteEventStore(sys.argv[1])
store.append_many((
    Event(event_type="task.uncommitted", actor_id="tester"),
    Event(event_type="task.uncommitted", actor_id="tester"),
))
'''
    result = subprocess.run([sys.executable, "-c", code, str(path)], timeout=15, check=False)
    assert result.returncode == 42
    with SQLiteEventStore(path) as store:
        assert store.read_from() == (existing,)
        assert store.append(make_event("task.started")) == 1
