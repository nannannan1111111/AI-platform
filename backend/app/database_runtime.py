"""Shared PostgreSQL runtime primitives for multi-process deployments."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool


def configure_postgresql_engine(
    database_url: str,
    *,
    pool_size: int,
    max_overflow: int,
    pool_timeout_seconds: float,
) -> Engine:
    """Create a bounded, stale-connection-safe production engine."""
    from sqlalchemy import create_engine

    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout_seconds,
        pool_recycle=1800,
    )


def configure_postgresql_lock_engine(database_url: str) -> Engine:
    """Create an unpooled engine for long-lived advisory-lock connections.

    Worker instance and dispatch locks intentionally hold their PostgreSQL
    sessions for much longer than an ordinary unit of work.  Keeping those
    sessions out of the bounded application pool prevents an in-flight
    Provider request from starving result persistence of a connection.
    """
    from sqlalchemy import create_engine

    return create_engine(
        database_url,
        poolclass=NullPool,
        pool_pre_ping=True,
        isolation_level="AUTOCOMMIT",
    )


class PostgresWorkerAdvisoryLocks:
    """Multiplex one Worker process' session locks over one DB connection.

    PostgreSQL session advisory locks are re-entrant within the same session,
    so the local key set is part of the correctness contract: concurrent tasks
    in one Worker must not accidentally share the same account or Provider
    slot merely because they use the same database connection.
    """

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._guard = Lock()
        self._held_keys: set[str] = set()

    def _try_acquire(self, key: str) -> bool:
        if key in self._held_keys:
            return False
        acquired = bool(
            self._connection.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                {"key": key},
            )
        )
        if acquired:
            self._held_keys.add(key)
        return acquired

    def _release(self, key: str) -> None:
        if key not in self._held_keys:
            return
        try:
            self._connection.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                {"key": key},
            )
        finally:
            self._held_keys.discard(key)

    @contextmanager
    def worker_slot(self, slots: int) -> Iterator[int | None]:
        """Hold one stable Worker ordinal on the shared lock session."""
        if slots <= 0:
            raise ValueError("slots must be positive")
        acquired_key: str | None = None
        acquired_slot: int | None = None
        try:
            with self._guard:
                for slot in range(1, slots + 1):
                    key = f"generation-worker-instance:{slot}"
                    if self._try_acquire(key):
                        acquired_key = key
                        acquired_slot = slot
                        break
            yield acquired_slot
        finally:
            if acquired_key is not None:
                with self._guard:
                    self._release(acquired_key)

    @contextmanager
    def generation_dispatch_lock(
        self,
        task_key: str,
        account_pool_key: str,
        account_slots: int,
        provider_pool_key: str,
        provider_slots: int,
    ) -> Iterator[tuple[int, int] | None]:
        """Hold task, user, and Provider slots on the shared lock session."""
        if account_slots <= 0 or provider_slots <= 0:
            raise ValueError("account and provider slots must be positive")
        acquired_keys: list[str] = []
        result: tuple[int, int] | None = None
        try:
            with self._guard:
                if self._try_acquire(task_key):
                    acquired_keys.append(task_key)
                    account_slot = next(
                        (
                            slot
                            for slot in range(account_slots)
                            if self._try_acquire(f"{account_pool_key}:{slot}")
                        ),
                        None,
                    )
                    if account_slot is not None:
                        account_key = f"{account_pool_key}:{account_slot}"
                        acquired_keys.append(account_key)
                        provider_slot = next(
                            (
                                slot
                                for slot in range(provider_slots)
                                if self._try_acquire(f"{provider_pool_key}:{slot}")
                            ),
                            None,
                        )
                        if provider_slot is not None:
                            provider_key = f"{provider_pool_key}:{provider_slot}"
                            acquired_keys.append(provider_key)
                            result = (account_slot, provider_slot)
            yield result
        finally:
            with self._guard:
                for key in reversed(acquired_keys):
                    self._release(key)


@contextmanager
def postgres_worker_advisory_locks(engine: Engine) -> Iterator[PostgresWorkerAdvisoryLocks]:
    """Open the one long-lived advisory-lock session for a Worker process."""
    with engine.connect() as connection:
        yield PostgresWorkerAdvisoryLocks(connection)


@contextmanager
def postgres_advisory_lock(engine: Engine, key: str) -> Iterator[bool]:
    """Try to hold a session-scoped advisory lock for one cluster-wide job."""
    with engine.connect() as connection:
        acquired = bool(
            connection.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                {"key": key},
            )
        )
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                    {"key": key},
                )


@contextmanager
def postgres_advisory_lock_slot(engine: Engine, pool_key: str, slots: int) -> Iterator[int | None]:
    """Hold one available session-scoped slot in a cluster-wide bounded pool."""
    if slots <= 0:
        raise ValueError("slots must be positive")
    with engine.connect() as connection:
        acquired_slot: int | None = None
        for slot in range(slots):
            acquired = bool(
                connection.scalar(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                    {"key": f"{pool_key}:{slot}"},
                )
            )
            if acquired:
                acquired_slot = slot
                break
        try:
            yield acquired_slot
        finally:
            if acquired_slot is not None:
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                    {"key": f"{pool_key}:{acquired_slot}"},
                )


@contextmanager
def postgres_advisory_task_and_slot_lock(
    engine: Engine,
    task_key: str,
    pool_key: str,
    slots: int,
) -> Iterator[int | None]:
    """Hold a unique task lock and one bounded provider-pool slot on one connection."""
    if slots <= 0:
        raise ValueError("slots must be positive")
    with engine.connect() as connection:
        task_acquired = bool(
            connection.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                {"key": task_key},
            )
        )
        acquired_slot: int | None = None
        if task_acquired:
            for slot in range(slots):
                acquired = bool(
                    connection.scalar(
                        text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                        {"key": f"{pool_key}:{slot}"},
                    )
                )
                if acquired:
                    acquired_slot = slot
                    break
        try:
            yield acquired_slot
        finally:
            if acquired_slot is not None:
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                    {"key": f"{pool_key}:{acquired_slot}"},
                )
            if task_acquired:
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                    {"key": task_key},
                )


@contextmanager
def postgres_advisory_generation_dispatch_lock(
    engine: Engine,
    task_key: str,
    account_pool_key: str,
    account_slots: int,
    provider_pool_key: str,
    provider_slots: int,
) -> Iterator[tuple[int, int] | None]:
    """Hold one task lock plus one user slot and one shared Provider slot."""
    if account_slots <= 0 or provider_slots <= 0:
        raise ValueError("account and provider slots must be positive")
    with engine.connect() as connection:
        acquired_keys: list[str] = []

        def acquire(key: str) -> bool:
            acquired = bool(
                connection.scalar(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                    {"key": key},
                )
            )
            if acquired:
                acquired_keys.append(key)
            return acquired

        result: tuple[int, int] | None = None
        if acquire(task_key):
            account_slot = next(
                (slot for slot in range(account_slots) if acquire(f"{account_pool_key}:{slot}")),
                None,
            )
            if account_slot is not None:
                provider_slot = next(
                    (slot for slot in range(provider_slots) if acquire(f"{provider_pool_key}:{slot}")),
                    None,
                )
                if provider_slot is not None:
                    result = (account_slot, provider_slot)
        try:
            yield result
        finally:
            for key in reversed(acquired_keys):
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                    {"key": key},
                )


@contextmanager
def postgres_advisory_worker_slot(engine: Engine, slots: int) -> Iterator[int | None]:
    """Assign one stable cluster-wide ordinal to a running worker process."""
    if slots <= 0:
        raise ValueError("slots must be positive")
    with engine.connect() as connection:
        acquired_slot: int | None = None
        for slot in range(1, slots + 1):
            acquired = bool(
                connection.scalar(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                    {"key": f"generation-worker-instance:{slot}"},
                )
            )
            if acquired:
                acquired_slot = slot
                break
        try:
            yield acquired_slot
        finally:
            if acquired_slot is not None:
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                    {"key": f"generation-worker-instance:{acquired_slot}"},
                )


def lock_account_generation_submissions(database: Session, account_space_id: str) -> None:
    """Serialize the active-task count and insert for one PostgreSQL account."""
    bind = database.get_bind()
    if bind.dialect.name != "postgresql":
        return
    database.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"generation-account:{account_space_id}"},
    )


def lock_global_generation_submissions(database: Session) -> None:
    """Serialize the global active-image count and task insert in PostgreSQL."""
    bind = database.get_bind()
    if bind.dialect.name != "postgresql":
        return
    database.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": "generation-global-submissions"},
    )
