"""Administrator-managed capacity for durable image-generation workers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Protocol

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.database_runtime import lock_global_generation_submissions


class InvalidWorkerCapacity(ValueError):
    """The requested worker capacity cannot be served by this deployment."""


@dataclass(frozen=True, slots=True)
class WorkerCapacity:
    """Current logical worker count and per-worker execution limit."""

    enabled_workers: int
    concurrency_per_worker: int
    global_active_image_limit: int
    task_deadline_minutes: int
    deployed_worker_limit: int
    total_concurrency: int
    updated_at: datetime


class WorkerCapacitySettings(Protocol):
    """Read and update the cluster-wide generation worker capacity."""

    def current(self) -> WorkerCapacity:
        """Return the current cluster-wide capacity."""

    def update(
        self,
        enabled_workers: int,
        concurrency_per_worker: int,
        global_active_image_limit: int,
        task_deadline_minutes: int,
    ) -> WorkerCapacity:
        """Persist a new capacity within the deployment's safe bounds."""

    def usage(self) -> dict[str, int]:
        """Return current queued and running image-unit usage."""


class InMemoryWorkerCapacitySettings:
    """Thread-safe capacity settings for HTTP and worker tests."""

    def __init__(self, deployed_worker_limit: int = 4) -> None:
        """Seed the in-memory setting with four workers and five slots each."""
        self._deployed_worker_limit = deployed_worker_limit
        self._enabled_workers = deployed_worker_limit
        self._concurrency_per_worker = 5
        self._global_active_image_limit = 500
        self._task_deadline_minutes = 10
        self._updated_at = datetime.now(UTC)
        self._lock = RLock()

    def current(self) -> WorkerCapacity:
        """Return the current in-memory capacity."""
        with self._lock:
            return _snapshot(
                self._enabled_workers,
                self._concurrency_per_worker,
                self._global_active_image_limit,
                self._task_deadline_minutes,
                self._deployed_worker_limit,
                self._updated_at,
            )

    def update(
        self,
        enabled_workers: int,
        concurrency_per_worker: int,
        global_active_image_limit: int,
        task_deadline_minutes: int,
    ) -> WorkerCapacity:
        """Validate and replace the in-memory capacity."""
        _validate(
            enabled_workers,
            concurrency_per_worker,
            global_active_image_limit,
            task_deadline_minutes,
            self._deployed_worker_limit,
        )
        with self._lock:
            self._enabled_workers = enabled_workers
            self._concurrency_per_worker = concurrency_per_worker
            self._global_active_image_limit = global_active_image_limit
            self._task_deadline_minutes = task_deadline_minutes
            self._updated_at = datetime.now(UTC)
            return self.current()

    def usage(self) -> dict[str, int]:
        """In-memory settings do not own generation tasks, so report no usage."""
        return {"queued_image_units": 0, "running_image_units": 0, "active_image_units": 0}


_metadata = MetaData()
_settings = Table(
    "generation_worker_capacity",
    _metadata,
    Column("settings_key", String(32), primary_key=True),
    Column("enabled_workers", Integer, nullable=False),
    Column("concurrency_per_worker", Integer, nullable=False),
    Column("global_active_image_limit", Integer, nullable=False),
    Column("task_deadline_minutes", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
_generation_tasks = Table(
    "generation_tasks",
    _metadata,
    Column("status", String(32), nullable=False),
    Column("quantity", Integer, nullable=False),
)


class SqlAlchemyWorkerCapacitySettings:
    """Persist worker capacity in the shared application database."""

    def __init__(self, sessions: sessionmaker[Session], *, deployed_worker_limit: int) -> None:
        """Use shared sessions and a deployment-provided physical worker limit."""
        if deployed_worker_limit <= 0:
            raise ValueError("deployed_worker_limit must be positive")
        self._sessions = sessions
        self._deployed_worker_limit = deployed_worker_limit

    @classmethod
    def for_database_url(
        cls,
        database_url: str,
        *,
        deployed_worker_limit: int,
    ) -> SqlAlchemyWorkerCapacitySettings:
        """Create settings for an already-migrated database."""
        return cls(
            sessionmaker(create_engine(database_url), expire_on_commit=False),
            deployed_worker_limit=deployed_worker_limit,
        )

    def current(self) -> WorkerCapacity:
        """Return the persisted capacity clamped to the deployed worker limit."""
        with self._sessions() as database:
            row = database.execute(
                select(_settings).where(_settings.c.settings_key == "global")
            ).mappings().one()
        enabled_workers = min(int(row["enabled_workers"]), self._deployed_worker_limit)
        return _snapshot(
            enabled_workers,
            int(row["concurrency_per_worker"]),
            int(row["global_active_image_limit"]),
            int(row["task_deadline_minutes"]),
            self._deployed_worker_limit,
            _aware(row["updated_at"]),
        )

    def update(
        self,
        enabled_workers: int,
        concurrency_per_worker: int,
        global_active_image_limit: int,
        task_deadline_minutes: int,
    ) -> WorkerCapacity:
        """Persist a validated worker capacity update."""
        _validate(
            enabled_workers,
            concurrency_per_worker,
            global_active_image_limit,
            task_deadline_minutes,
            self._deployed_worker_limit,
        )
        now = datetime.now(UTC)
        with self._sessions.begin() as database:
            lock_global_generation_submissions(database)
            database.execute(
                update(_settings)
                .where(_settings.c.settings_key == "global")
                .values(
                    enabled_workers=enabled_workers,
                    concurrency_per_worker=concurrency_per_worker,
                    global_active_image_limit=global_active_image_limit,
                    task_deadline_minutes=task_deadline_minutes,
                    updated_at=now,
                )
            )
        return _snapshot(
            enabled_workers,
            concurrency_per_worker,
            global_active_image_limit,
            task_deadline_minutes,
            self._deployed_worker_limit,
            now,
        )

    def usage(self) -> dict[str, int]:
        """Aggregate queued and running image units for the administrator view."""
        with self._sessions() as database:
            rows = database.execute(
                select(
                    _generation_tasks.c.status,
                    func.coalesce(func.sum(_generation_tasks.c.quantity), 0),
                )
                .where(_generation_tasks.c.status.in_(("queued", "running")))
                .group_by(_generation_tasks.c.status)
            )
        counts = {str(status): int(units) for status, units in rows}
        queued = counts.get("queued", 0)
        running = counts.get("running", 0)
        return {
            "queued_image_units": queued,
            "running_image_units": running,
            "active_image_units": queued + running,
        }


def _validate(
    enabled_workers: int,
    concurrency_per_worker: int,
    global_active_image_limit: int,
    task_deadline_minutes: int,
    deployed_worker_limit: int,
) -> None:
    if not 1 <= enabled_workers <= deployed_worker_limit:
        raise InvalidWorkerCapacity(f"启用 Worker 数必须在 1 到 {deployed_worker_limit} 之间")
    if not 1 <= concurrency_per_worker <= 50:
        raise InvalidWorkerCapacity("单 Worker 并发数必须在 1 到 50 之间")
    if enabled_workers * concurrency_per_worker > 200:
        raise InvalidWorkerCapacity("Worker 总并发数不能超过 200")
    if not 1 <= global_active_image_limit <= 100_000:
        raise InvalidWorkerCapacity("全站活动图片名额必须在 1 到 100000 之间")
    if not 1 <= task_deadline_minutes <= 120:
        raise InvalidWorkerCapacity("生成任务截止时间必须在 1 到 120 分钟之间")


def _snapshot(
    enabled: int,
    concurrency: int,
    global_active_image_limit: int,
    task_deadline_minutes: int,
    deployed: int,
    updated_at: datetime,
) -> WorkerCapacity:
    return WorkerCapacity(
        enabled_workers=enabled,
        concurrency_per_worker=concurrency,
        global_active_image_limit=global_active_image_limit,
        task_deadline_minutes=task_deadline_minutes,
        deployed_worker_limit=deployed,
        total_concurrency=enabled * concurrency,
        updated_at=updated_at,
    )


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise RuntimeError("worker capacity timestamp is invalid")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
