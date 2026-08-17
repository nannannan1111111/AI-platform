"""Shared PostgreSQL runtime primitives for multi-process deployments."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session


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
