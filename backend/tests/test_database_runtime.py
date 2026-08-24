from pathlib import Path

from sqlalchemy import text
from sqlalchemy.pool import NullPool

from app.database_runtime import (
    PostgresWorkerAdvisoryLocks,
    configure_postgresql_engine,
    configure_postgresql_lock_engine,
    lock_account_generation_submissions,
)


class _Dialect:
    name = "sqlite"


class _Bind:
    dialect = _Dialect()


class _Database:
    def __init__(self) -> None:
        self.executed = False

    def get_bind(self) -> _Bind:
        return _Bind()

    def execute(self, *_args: object, **_kwargs: object) -> None:
        self.executed = True


def test_account_submission_lock_keeps_sqlite_tests_portable() -> None:
    database = _Database()

    lock_account_generation_submissions(database, "account-1")  # type: ignore[arg-type]

    assert database.executed is False


def test_long_lived_lock_engine_does_not_consume_the_business_pool() -> None:
    engine = configure_postgresql_lock_engine("sqlite+pysqlite:///:memory:")

    try:
        assert isinstance(engine.pool, NullPool)
    finally:
        engine.dispose()


def test_shared_worker_lock_connection_leaves_business_persistence_available(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'worker-pools.sqlite3').as_posix()}"
    business_engine = configure_postgresql_engine(
        database_url,
        pool_size=2,
        max_overflow=1,
        pool_timeout_seconds=0.05,
    )
    lock_engine = configure_postgresql_lock_engine(database_url)

    try:
        with lock_engine.connect():
            assert business_engine.pool.checkedout() == 0  # type: ignore[attr-defined]
            with business_engine.begin() as connection:
                assert connection.scalar(text("SELECT 1")) == 1
                assert business_engine.pool.checkedout() == 1  # type: ignore[attr-defined]
    finally:
        lock_engine.dispose()
        business_engine.dispose()


class _AdvisoryConnection:
    def __init__(self) -> None:
        self.acquired: list[str] = []
        self.released: list[str] = []

    def scalar(self, _statement: object, parameters: dict[str, str]) -> bool:
        self.acquired.append(parameters["key"])
        return True

    def execute(self, _statement: object, parameters: dict[str, str]) -> None:
        self.released.append(parameters["key"])


def test_shared_worker_lock_session_prevents_reentrant_slot_reuse() -> None:
    connection = _AdvisoryConnection()
    locks = PostgresWorkerAdvisoryLocks(connection)  # type: ignore[arg-type]

    with locks.worker_slot(2) as worker_slot:
        assert worker_slot == 1
        with locks.generation_dispatch_lock("task-1", "account", 1, "provider", 1) as first:
            assert first == (0, 0)
            with locks.generation_dispatch_lock("task-2", "account", 1, "provider", 1) as second:
                assert second is None

    assert connection.acquired == [
        "generation-worker-instance:1",
        "task-1",
        "account:0",
        "provider:0",
        "task-2",
    ]
    assert connection.released == [
        "task-2",
        "provider:0",
        "account:0",
        "task-1",
        "generation-worker-instance:1",
    ]
