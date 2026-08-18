"""SQLAlchemy Adapter for the GenerationTasks interface."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
    case,
    create_engine,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.canvases import Canvases, CanvasNotFound
from app.credits import GenerationCredits
from app.database_runtime import lock_account_generation_submissions, lock_global_generation_submissions
from app.generation._validation import validated_submission
from app.generation.deadlines import (
    GENERATION_TASK_DEADLINE,
    GENERATION_TIMEOUT_REASON,
    generation_timeout_reference,
)
from app.generation.models import (
    GenerationActivitySummary,
    GenerationCancelled,
    GenerationConcurrencyLimit,
    GenerationDispatchStarted,
    GenerationFailed,
    GenerationGlobalCapacityLimit,
    GenerationParameters,
    GenerationStarted,
    GenerationSubmission,
    GenerationSucceeded,
    GenerationTask,
    GenerationTaskAlreadyExists,
    GenerationTaskNotFound,
    GenerationTaskStatus,
    GenerationTransition,
)

_metadata = MetaData()
_generation_tasks = Table(
    "generation_tasks",
    _metadata,
    Column("id", String(255), primary_key=True),
    Column("user_id", String(36), nullable=False),
    Column("account_space_id", String(36), nullable=False),
    Column("canvas_id", String(255), nullable=True),
    Column("logical_model", String(128), nullable=False),
    Column("output_spec", String(128), nullable=False),
    Column("quantity", BigInteger, nullable=False),
    Column("prompt", Text, nullable=False),
    Column("aspect_ratio", String(8), nullable=False),
    Column("quality", String(16), nullable=False),
    Column("size", String(32), nullable=False),
    Column("resolution_tier", String(16), nullable=False),
    Column("output_format", String(16), nullable=False),
    Column("operation", String(16), nullable=False),
    Column("input_fidelity", String(16), nullable=False),
    Column("reference_media_ids", Text, nullable=False),
    Column("mask_media_id", String(255), nullable=False),
    Column("credit_freeze_id", String(36), nullable=False),
    Column("model_price_version_id", String(36), nullable=False),
    Column("frozen_units", BigInteger, nullable=False),
    Column("status", String(32), nullable=False),
    Column("selected_route_id", String(36), nullable=False),
    Column("route_selection_reason", String(32), nullable=False),
    Column("provider_task_id", String(255), nullable=False),
    Column("delivered_quantity", BigInteger, nullable=True),
    Column("error", String(255), nullable=False),
    Column("outcome_reference", String(255), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("history_hidden_at", DateTime(timezone=True), nullable=True),
)
_generation_capacity = Table(
    "generation_worker_capacity",
    _metadata,
    Column("settings_key", String(32), primary_key=True),
    Column("global_active_image_limit", BigInteger, nullable=False),
)


class SqlAlchemyGenerationTasks:
    """Persist account-owned generation tasks and their credit lifecycle."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        credits: GenerationCredits,
        canvases: Canvases,
        max_active_tasks: int,
        deadline: Callable[[], timedelta] = lambda: GENERATION_TASK_DEADLINE,
    ) -> None:
        if max_active_tasks <= 0:
            raise ValueError("max_active_tasks must be positive")
        self._session_factory = session_factory
        self._credits = credits
        self._canvases = canvases
        self._max_active_tasks = max_active_tasks
        self._deadline = deadline

    @classmethod
    def for_database_url(
        cls,
        database_url: str,
        *,
        credits: GenerationCredits,
        canvases: Canvases,
        max_active_tasks: int,
        deadline: Callable[[], timedelta] = lambda: GENERATION_TASK_DEADLINE,
    ) -> SqlAlchemyGenerationTasks:
        engine = create_engine(database_url)
        return cls(
            sessionmaker(engine, expire_on_commit=False),
            credits=credits,
            canvases=canvases,
            max_active_tasks=max_active_tasks,
            deadline=deadline,
        )

    def submit(self, submission: GenerationSubmission) -> GenerationTask:
        submission = validated_submission(submission)
        with self._session_factory.begin() as database:
            lock_global_generation_submissions(database)
            lock_account_generation_submissions(database, submission.account_space_id)
            existing_row = _existing(database, submission.account_space_id, submission.task_id)
            if existing_row is not None:
                existing = _task_from_row(existing_row)
                if _matches_submission(existing, submission):
                    return existing
                raise GenerationTaskAlreadyExists(submission.task_id)
            if submission.canvas_id is not None:
                canvas = self._canvases.get(submission.account_space_id, submission.canvas_id)
                if canvas.user_id != submission.user_id:
                    raise CanvasNotFound(submission.canvas_id)
            global_limit = database.scalar(
                select(_generation_capacity.c.global_active_image_limit)
                .where(_generation_capacity.c.settings_key == "global")
            )
            global_active_units = database.scalar(
                select(func.coalesce(func.sum(_generation_tasks.c.quantity), 0))
                .where(_generation_tasks.c.status.in_(("queued", "running")))
            )
            if int(global_active_units or 0) + submission.quantity > int(global_limit or 500):
                raise GenerationGlobalCapacityLimit()
            active_units = database.scalar(
                select(func.coalesce(func.sum(_generation_tasks.c.quantity), 0))
                .select_from(_generation_tasks)
                .where(
                    _generation_tasks.c.account_space_id == submission.account_space_id,
                    _generation_tasks.c.status.in_(("queued", "running")),
                )
            )
            if int(active_units or 0) + submission.quantity > self._max_active_tasks:
                raise GenerationConcurrencyLimit(submission.account_space_id)
            freeze = self._credits.freeze(
                submission.account_space_id,
                submission.logical_model,
                submission.output_spec,
                quantity=submission.quantity,
                task_reference=_freeze_reference(submission),
                occurred_at=submission.submitted_at,
            )
            task = GenerationTask(
                task_id=submission.task_id,
                user_id=submission.user_id,
                account_space_id=submission.account_space_id,
                canvas_id=submission.canvas_id,
                logical_model=submission.logical_model,
                output_spec=submission.output_spec,
                quantity=submission.quantity,
                prompt=submission.prompt,
                params=submission.params,
                reference_media_ids=submission.reference_media_ids,
                mask_media_id=submission.mask_media_id,
                credit_freeze_id=freeze.freeze_id,
                model_price_version_id=freeze.model_price_version_id,
                frozen_credits=freeze.frozen_credits,
                status=GenerationTaskStatus.QUEUED,
                created_at=submission.submitted_at,
                updated_at=submission.submitted_at,
                selected_route_id=submission.selected_route_id,
                route_selection_reason=submission.route_selection_reason,
            )
            try:
                database.execute(
                    insert(_generation_tasks).values(_task_values(task, frozen_units=freeze.frozen_credits))
                )
            except IntegrityError as exc:
                raise GenerationTaskAlreadyExists(submission.task_id) from exc
            return task

    def get(self, account_space_id: str, task_id: str) -> GenerationTask:
        with self._session_factory() as database:
            row = _existing(database, account_space_id, task_id)
        if row is None:
            raise GenerationTaskNotFound(task_id)
        return _task_from_row(row)

    def active_across_accounts(self) -> tuple[GenerationTask, ...]:
        """读取所有账户仍在排队或运行的任务。"""
        with self._session_factory() as database:
            rows = database.execute(
                select(_generation_tasks)
                .where(_generation_tasks.c.status.in_(("queued", "running")))
                .order_by(
                    _generation_tasks.c.created_at,
                    _generation_tasks.c.account_space_id,
                    _generation_tasks.c.id,
                )
            ).mappings()
            return tuple(_task_from_row(row) for row in rows)

    def expire_due(self, now: datetime) -> tuple[GenerationTask, ...]:
        """按当前管理员截止时间幂等失败活动任务并释放冻结额度。"""
        deadline = self._deadline()
        cutoff = now - deadline
        with self._session_factory() as database:
            candidates = tuple(
                database.execute(
                    select(
                        _generation_tasks.c.account_space_id,
                        _generation_tasks.c.id,
                    )
                    .where(
                        _generation_tasks.c.status.in_(("queued", "running")),
                        _generation_tasks.c.started_at.is_not(None),
                        _generation_tasks.c.started_at <= cutoff,
                    )
                    .order_by(
                        _generation_tasks.c.started_at,
                        _generation_tasks.c.account_space_id,
                        _generation_tasks.c.id,
                    )
                )
            )
        expired: list[GenerationTask] = []
        for account_space_id, task_id in candidates:
            result = self._expire_one(str(account_space_id), str(task_id), now, deadline)
            if result is not None:
                expired.append(result)
        return tuple(expired)

    def _expire_one(
        self,
        account_space_id: str,
        task_id: str,
        now: datetime,
        deadline: timedelta,
    ) -> GenerationTask | None:
        with self._session_factory.begin() as database:
            row = _existing(database, account_space_id, task_id, for_update=True)
            if row is None:
                return None
            task = _task_from_row(row)
            if (
                task.status.is_terminal
                or task.started_at is None
                or task.started_at + deadline > now
            ):
                return None
            outcome_reference = generation_timeout_reference(task)
            self._credits.release(
                task.credit_freeze_id,
                release_reference=outcome_reference,
                reason=GENERATION_TIMEOUT_REASON,
                occurred_at=now,
            )
            updated = replace(
                task,
                status=GenerationTaskStatus.FAILED,
                error=GENERATION_TIMEOUT_REASON,
                outcome_reference=outcome_reference,
                updated_at=now,
            )
            database.execute(
                update(_generation_tasks)
                .where(
                    _generation_tasks.c.account_space_id == account_space_id,
                    _generation_tasks.c.id == task_id,
                )
                .values(**_task_values(updated, frozen_units=updated.frozen_credits))
            )
            return updated

    def active_for_canvas(self, account_space_id: str, canvas_id: str) -> tuple[GenerationTask, ...]:
        with self._session_factory() as database:
            rows = database.execute(
                select(_generation_tasks)
                .where(
                    _generation_tasks.c.account_space_id == account_space_id,
                    _generation_tasks.c.canvas_id == canvas_id,
                    _generation_tasks.c.status.in_(("queued", "running")),
                )
                .order_by(_generation_tasks.c.created_at, _generation_tasks.c.id)
            ).mappings()
            return tuple(_task_from_row(row) for row in rows)

    def recent_for_canvas(
        self,
        account_space_id: str,
        canvas_id: str,
        *,
        limit: int,
    ) -> tuple[GenerationTask, ...]:
        if limit <= 0:
            raise ValueError("recent generation task limit must be positive")
        with self._session_factory() as database:
            rows = database.execute(
                select(_generation_tasks)
                .where(
                    _generation_tasks.c.account_space_id == account_space_id,
                    _generation_tasks.c.canvas_id == canvas_id,
                    _generation_tasks.c.history_hidden_at.is_(None),
                )
                .order_by(_generation_tasks.c.created_at.desc(), _generation_tasks.c.id.desc())
                .limit(limit)
            ).mappings()
            return tuple(_task_from_row(row) for row in rows)

    def recent_for_account(
        self,
        account_space_id: str,
        *,
        limit: int,
    ) -> tuple[GenerationTask, ...]:
        if limit <= 0:
            raise ValueError("recent generation task limit must be positive")
        with self._session_factory() as database:
            rows = database.execute(
                select(_generation_tasks)
                .where(
                    _generation_tasks.c.account_space_id == account_space_id,
                    _generation_tasks.c.history_hidden_at.is_(None),
                )
                .order_by(_generation_tasks.c.created_at.desc(), _generation_tasks.c.id.desc())
                .limit(limit)
            ).mappings()
            return tuple(_task_from_row(row) for row in rows)

    def clear_history(self, account_space_id: str, *, cleared_at: datetime) -> int:
        """Hide terminal tasks from user-facing history without deleting their facts."""
        with self._session_factory.begin() as database:
            hidden_task_ids = database.scalars(
                update(_generation_tasks)
                .where(
                    _generation_tasks.c.account_space_id == account_space_id,
                    _generation_tasks.c.status.in_(("succeeded", "failed", "cancelled")),
                    _generation_tasks.c.history_hidden_at.is_(None),
                )
                .values(history_hidden_at=cleared_at)
                .returning(_generation_tasks.c.id)
            )
            return len(tuple(hidden_task_ids))

    def activity_summary(
        self,
        account_space_id: str,
        *,
        since: datetime | None,
    ) -> GenerationActivitySummary:
        conditions = [_generation_tasks.c.account_space_id == account_space_id]
        if since is not None:
            conditions.append(_generation_tasks.c.created_at >= since)
        consumed = case(
            (
                _generation_tasks.c.status == GenerationTaskStatus.SUCCEEDED.value,
                _generation_tasks.c.frozen_units
                * func.coalesce(_generation_tasks.c.delivered_quantity, 0)
                / _generation_tasks.c.quantity,
            ),
            else_=0,
        )
        with self._session_factory() as database:
            row = database.execute(
                select(
                    func.count(),
                    func.sum(case((_generation_tasks.c.status == GenerationTaskStatus.SUCCEEDED.value, 1), else_=0)),
                    func.sum(case((_generation_tasks.c.status == GenerationTaskStatus.FAILED.value, 1), else_=0)),
                    func.sum(consumed),
                ).where(*conditions)
            ).one()
        return GenerationActivitySummary(
            total_tasks=int(row[0] or 0),
            succeeded_tasks=int(row[1] or 0),
            failed_tasks=int(row[2] or 0),
            consumed_credit_units=int(row[3] or 0),
        )

    def transition(self, account_space_id: str, task_id: str, event: GenerationTransition) -> GenerationTask:
        with self._session_factory.begin() as database:
            row = _existing(database, account_space_id, task_id, for_update=True)
            if row is None:
                raise GenerationTaskNotFound(task_id)
            task = _task_from_row(row)
            if task.status.is_terminal:
                if _terminal_matches(task, event):
                    return task
                raise ValueError("terminal task outcome is immutable")
            if isinstance(event, GenerationDispatchStarted):
                if task.status is not GenerationTaskStatus.QUEUED:
                    raise ValueError("only queued tasks can begin provider dispatch")
                updated = replace(
                    task,
                    status=GenerationTaskStatus.RUNNING,
                    started_at=task.started_at or event.occurred_at,
                    updated_at=event.occurred_at,
                )
            elif isinstance(event, GenerationStarted):
                if task.status not in {GenerationTaskStatus.QUEUED, GenerationTaskStatus.RUNNING}:
                    raise ValueError("only queued or dispatching tasks can start")
                if task.status is GenerationTaskStatus.RUNNING and task.provider_task_id:
                    raise ValueError("provider task already recorded")
                updated = replace(
                    task,
                    status=GenerationTaskStatus.RUNNING,
                    provider_task_id=event.provider_task_id,
                    started_at=task.started_at or event.occurred_at,
                    updated_at=event.occurred_at,
                )
            elif isinstance(event, GenerationSucceeded):
                if task.status is not GenerationTaskStatus.RUNNING:
                    raise ValueError("only running tasks can succeed")
                self._credits.settle(
                    task.credit_freeze_id,
                    delivered_quantity=event.delivered_quantity,
                    settlement_reference=event.outcome_reference,
                    occurred_at=event.occurred_at,
                )
                updated = replace(
                    task,
                    status=GenerationTaskStatus.SUCCEEDED,
                    delivered_quantity=event.delivered_quantity,
                    outcome_reference=event.outcome_reference,
                    updated_at=event.occurred_at,
                )
            elif isinstance(event, (GenerationFailed, GenerationCancelled)):
                self._credits.release(
                    task.credit_freeze_id,
                    release_reference=event.outcome_reference,
                    reason=event.reason,
                    occurred_at=event.occurred_at,
                )
                updated = replace(
                    task,
                    status=GenerationTaskStatus.FAILED
                    if isinstance(event, GenerationFailed)
                    else GenerationTaskStatus.CANCELLED,
                    error=event.reason,
                    outcome_reference=event.outcome_reference,
                    updated_at=event.occurred_at,
                )
            else:
                raise NotImplementedError(type(event).__name__)
            database.execute(
                update(_generation_tasks)
                .where(_generation_tasks.c.id == task_id)
                .values(**_task_values(updated, frozen_units=updated.frozen_credits))
            )
            return updated


def _existing(database: Session, account_space_id: str, task_id: str, *, for_update: bool = False) -> Any:
    query = select(_generation_tasks).where(
        _generation_tasks.c.account_space_id == account_space_id,
        _generation_tasks.c.id == task_id,
    )
    if for_update:
        query = query.with_for_update()
    return database.execute(query).mappings().one_or_none()


def _task_values(task: GenerationTask, *, frozen_units: str) -> dict[str, Any]:
    from app.credits._amounts import credit_units

    return {
        "id": task.task_id,
        "user_id": task.user_id,
        "account_space_id": task.account_space_id,
        "canvas_id": task.canvas_id,
        "logical_model": task.logical_model,
        "output_spec": task.output_spec,
        "quantity": task.quantity,
        "prompt": task.prompt,
        "aspect_ratio": task.params.aspect_ratio,
        "quality": task.params.quality,
        "size": task.params.size,
        "resolution_tier": task.params.resolution_tier,
        "output_format": task.params.output_format,
        "operation": task.params.operation,
        "input_fidelity": task.params.input_fidelity,
        "reference_media_ids": json.dumps(task.reference_media_ids),
        "mask_media_id": task.mask_media_id,
        "credit_freeze_id": task.credit_freeze_id,
        "model_price_version_id": task.model_price_version_id,
        "frozen_units": credit_units(frozen_units),
        "status": task.status.value,
        "selected_route_id": task.selected_route_id,
        "route_selection_reason": task.route_selection_reason,
        "provider_task_id": task.provider_task_id,
        "delivered_quantity": task.delivered_quantity,
        "error": task.error,
        "outcome_reference": task.outcome_reference,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "started_at": task.started_at,
    }


def _task_from_row(row: Any) -> GenerationTask:
    def aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    return GenerationTask(
        task_id=str(row["id"]),
        user_id=str(row["user_id"]),
        account_space_id=str(row["account_space_id"]),
        canvas_id=None if row["canvas_id"] is None else str(row["canvas_id"]),
        logical_model=str(row["logical_model"]),
        output_spec=str(row["output_spec"]),
        quantity=int(row["quantity"]),
        prompt=str(row["prompt"]),
        params=GenerationParameters(
            aspect_ratio=str(row["aspect_ratio"]),
            quality=str(row["quality"]),
            size=str(row["size"]),
            resolution_tier=str(row["resolution_tier"]),
            output_format=str(row["output_format"]),
            operation=str(row["operation"]),
            input_fidelity=str(row["input_fidelity"]),
        ),
        credit_freeze_id=str(row["credit_freeze_id"]),
        model_price_version_id=str(row["model_price_version_id"]),
        frozen_credits=f"{int(row['frozen_units']) // 10000}.{int(row['frozen_units']) % 10000:04d}",
        status=GenerationTaskStatus(str(row["status"])),
        created_at=aware(row["created_at"]),
        updated_at=aware(row["updated_at"]),
        started_at=None if row["started_at"] is None else aware(row["started_at"]),
        reference_media_ids=tuple(json.loads(str(row["reference_media_ids"]))),
        mask_media_id=str(row["mask_media_id"]),
        selected_route_id=str(row["selected_route_id"]),
        route_selection_reason=str(row["route_selection_reason"]),
        provider_task_id=str(row["provider_task_id"]),
        delivered_quantity=None if row["delivered_quantity"] is None else int(row["delivered_quantity"]),
        error=str(row["error"]),
        outcome_reference=str(row["outcome_reference"]),
    )


def _matches_submission(task: GenerationTask, submission: GenerationSubmission) -> bool:
    return (
        task.user_id == submission.user_id
        and task.canvas_id == submission.canvas_id
        and task.logical_model == submission.logical_model
        and task.output_spec == submission.output_spec
        and task.quantity == submission.quantity
        and task.prompt == submission.prompt
        and task.params == submission.params
        and task.reference_media_ids == submission.reference_media_ids
        and task.mask_media_id == submission.mask_media_id
    )


def _freeze_reference(submission: GenerationSubmission) -> str:
    return f"generation:{submission.account_space_id}:{submission.task_id}"


def _terminal_matches(task: GenerationTask, event: GenerationTransition) -> bool:
    if isinstance(event, GenerationSucceeded):
        return (
            task.status is GenerationTaskStatus.SUCCEEDED
            and task.delivered_quantity == event.delivered_quantity
            and task.outcome_reference == event.outcome_reference
        )
    if isinstance(event, (GenerationFailed, GenerationCancelled)):
        expected = (
            GenerationTaskStatus.FAILED if isinstance(event, GenerationFailed) else GenerationTaskStatus.CANCELLED
        )
        return (
            task.status is expected and task.error == event.reason and task.outcome_reference == event.outcome_reference
        )
    return False
