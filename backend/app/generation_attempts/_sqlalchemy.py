"""生成尝试 Interface 的 SQLAlchemy Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.generation import GenerationTaskNotFound, GenerationTasks
from app.generation_attempts._transitions import transitioned
from app.generation_attempts._validation import provider_idempotency_key, selected_route
from app.generation_attempts.models import (
    GenerationAttempt,
    GenerationAttemptConflict,
    GenerationAttemptNotFound,
    GenerationAttemptPreparation,
    GenerationAttemptStatus,
    GenerationAttemptTransition,
)
from app.provider_costs import ProviderCostRates

_metadata = MetaData()
_generation_attempts = Table(
    "image_generation_attempts",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("generation_task_id", String(255), ForeignKey("generation_tasks.id"), nullable=False),
    Column("attempt_no", Integer, nullable=False),
    Column("route_id", String(36), ForeignKey("image_model_routes.id"), nullable=False),
    Column("provider_cost_rate_id", String(36), ForeignKey("provider_cost_rates.id"), nullable=True),
    Column("provider_idempotency_key", String(128), nullable=False, unique=True),
    Column("status", String(32), nullable=False),
    Column("provider_task_id", String(255), nullable=False),
    Column("error_code", String(128), nullable=False),
    Column("error", String(1024), nullable=False),
    Column("submitted_at", DateTime(timezone=True), nullable=True),
    Column("accepted_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("generation_task_id", "attempt_no", name="uq_generation_attempts_task_number"),
)


class SqlAlchemyGenerationAttempts:
    """持久化生成尝试的有序历史与稳定 Provider 幂等键。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        generation_tasks: GenerationTasks,
        provider_cost_rates: ProviderCostRates,
        id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self._session_factory = session_factory
        self._generation_tasks = generation_tasks
        self._provider_cost_rates = provider_cost_rates
        self._id_factory = id_factory

    @classmethod
    def for_database_url(
        cls,
        database_url: str,
        *,
        generation_tasks: GenerationTasks,
        provider_cost_rates: ProviderCostRates,
        id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> SqlAlchemyGenerationAttempts:
        engine = create_engine(database_url)
        return cls(
            sessionmaker(engine, expire_on_commit=False),
            generation_tasks=generation_tasks,
            provider_cost_rates=provider_cost_rates,
            id_factory=id_factory,
        )

    def prepare(self, preparation: GenerationAttemptPreparation) -> GenerationAttempt:
        """幂等插入当前尝试，或在明确失败后插入下一次尝试。"""
        task = self._generation_tasks.get(preparation.account_space_id, preparation.task_id)
        route_id = selected_route(task, preparation)
        existing = self._latest(preparation.task_id)
        if existing is not None:
            _matching(existing, route_id)
            if existing.status is not GenerationAttemptStatus.FAILED:
                return existing
        cost_rate = self._provider_cost_rates.current_at(route_id, preparation.occurred_at)
        attempt_no = 1 if existing is None else existing.attempt_no + 1
        attempt = GenerationAttempt(
            attempt_id=self._id_factory(),
            task_id=preparation.task_id,
            attempt_no=attempt_no,
            route_id=route_id,
            provider_idempotency_key=provider_idempotency_key(preparation.task_id, attempt_no, route_id),
            provider_cost_rate_id=cost_rate.version_id,
            status=GenerationAttemptStatus.CREATED,
            created_at=preparation.occurred_at,
            updated_at=preparation.occurred_at,
        )
        try:
            with self._session_factory.begin() as database:
                database.execute(insert(_generation_attempts).values(**_attempt_values(attempt)))
        except IntegrityError as exc:
            concurrent = self._latest(preparation.task_id)
            if concurrent is not None:
                return _matching(concurrent, route_id)
            raise GenerationAttemptConflict(preparation.task_id) from exc
        return attempt

    def for_task(self, account_space_id: str, task_id: str) -> tuple[GenerationAttempt, ...]:
        """按尝试序号读取任务记录并隐藏其他账户空间。"""
        try:
            self._generation_tasks.get(account_space_id, task_id)
        except GenerationTaskNotFound as exc:
            raise GenerationAttemptNotFound(task_id) from exc
        with self._session_factory() as database:
            rows = (
                database.execute(
                    select(_generation_attempts)
                    .where(_generation_attempts.c.generation_task_id == task_id)
                    .order_by(_generation_attempts.c.attempt_no)
                )
                .mappings()
                .all()
            )
        return tuple(_attempt_from_row(row) for row in rows)

    def transition(
        self,
        account_space_id: str,
        attempt_id: str,
        event: GenerationAttemptTransition,
    ) -> GenerationAttempt:
        """锁定生成尝试并幂等记录提交阶段事实。"""
        existing = self._by_id(attempt_id)
        if existing is None:
            raise GenerationAttemptNotFound(attempt_id)
        try:
            self._generation_tasks.get(account_space_id, existing.task_id)
        except GenerationTaskNotFound as exc:
            raise GenerationAttemptNotFound(attempt_id) from exc
        with self._session_factory.begin() as database:
            row = (
                database.execute(
                    select(_generation_attempts).where(_generation_attempts.c.id == attempt_id).with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise GenerationAttemptNotFound(attempt_id)
            updated = transitioned(_attempt_from_row(row), event)
            database.execute(
                update(_generation_attempts)
                .where(_generation_attempts.c.id == attempt_id)
                .values(**_attempt_values(updated))
            )
            return updated

    def _latest(self, task_id: str) -> GenerationAttempt | None:
        with self._session_factory() as database:
            row = (
                database.execute(
                    select(_generation_attempts)
                    .where(_generation_attempts.c.generation_task_id == task_id)
                    .order_by(_generation_attempts.c.attempt_no.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _attempt_from_row(row)

    def _by_id(self, attempt_id: str) -> GenerationAttempt | None:
        with self._session_factory() as database:
            row = (
                database.execute(select(_generation_attempts).where(_generation_attempts.c.id == attempt_id))
                .mappings()
                .one_or_none()
            )
        return None if row is None else _attempt_from_row(row)


def _matching(existing: GenerationAttempt, route_id: str) -> GenerationAttempt:
    if existing.route_id != route_id:
        raise GenerationAttemptConflict(existing.task_id)
    return existing


def _attempt_values(attempt: GenerationAttempt) -> dict[str, Any]:
    return {
        "id": attempt.attempt_id,
        "generation_task_id": attempt.task_id,
        "attempt_no": attempt.attempt_no,
        "route_id": attempt.route_id,
        "provider_cost_rate_id": attempt.provider_cost_rate_id or None,
        "provider_idempotency_key": attempt.provider_idempotency_key,
        "status": attempt.status.value,
        "provider_task_id": attempt.provider_task_id,
        "error_code": attempt.error_code,
        "error": attempt.error,
        "submitted_at": attempt.submitted_at,
        "accepted_at": attempt.accepted_at,
        "finished_at": attempt.finished_at,
        "created_at": attempt.created_at,
        "updated_at": attempt.updated_at,
    }


def _attempt_from_row(row: Any) -> GenerationAttempt:
    def aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    return GenerationAttempt(
        attempt_id=str(row["id"]),
        task_id=str(row["generation_task_id"]),
        attempt_no=int(row["attempt_no"]),
        route_id=str(row["route_id"]),
        provider_cost_rate_id="" if row["provider_cost_rate_id"] is None else str(row["provider_cost_rate_id"]),
        provider_idempotency_key=str(row["provider_idempotency_key"]),
        status=GenerationAttemptStatus(str(row["status"])),
        created_at=aware(row["created_at"]),
        updated_at=aware(row["updated_at"]),
        provider_task_id=str(row["provider_task_id"]),
        error_code=str(row["error_code"]),
        error=str(row["error"]),
        submitted_at=None if row["submitted_at"] is None else aware(row["submitted_at"]),
        accepted_at=None if row["accepted_at"] is None else aware(row["accepted_at"]),
        finished_at=None if row["finished_at"] is None else aware(row["finished_at"]),
    )
