"""SaaS 生成任务 Interface 的内存 Adapter。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from threading import RLock

from app.canvases import Canvases, CanvasNotFound
from app.credits import GenerationCredits
from app.generation._validation import validated_submission
from app.generation.deadlines import (
    GENERATION_TASK_DEADLINE,
    GENERATION_TIMEOUT_REASON,
    generation_deadline_reached,
    generation_timeout_reference,
)
from app.generation.models import (
    GenerationActivitySummary,
    GenerationCancelled,
    GenerationDispatchStarted,
    GenerationFailed,
    GenerationStarted,
    GenerationSubmission,
    GenerationSucceeded,
    GenerationTask,
    GenerationTaskAlreadyExists,
    GenerationTaskNotFound,
    GenerationTaskStatus,
    GenerationTransition,
)


class InMemoryGenerationTasks:
    """在单进程内保存账户空间归属的生成任务。"""

    def __init__(
        self,
        credits: GenerationCredits,
        *,
        canvases: Canvases,
        max_active_tasks: int,
        deadline: Callable[[], timedelta] = lambda: GENERATION_TASK_DEADLINE,
    ) -> None:
        if max_active_tasks <= 0:
            raise ValueError("并发任务上限必须为正整数")
        self._credits = credits
        self._canvases = canvases
        self._max_active_tasks = max_active_tasks
        self._deadline = deadline
        self._tasks_by_key: dict[tuple[str, str], GenerationTask] = {}
        self._history_hidden_at_by_key: dict[tuple[str, str], datetime] = {}
        self._lock = RLock()

    def submit(self, submission: GenerationSubmission) -> GenerationTask:
        """创建任务并原子冻结预计额度。"""
        submission = validated_submission(submission)
        key = (submission.account_space_id, submission.task_id)
        with self._lock:
            existing = self._tasks_by_key.get(key)
            if existing is not None:
                if _matches_submission(existing, submission):
                    return existing
                raise GenerationTaskAlreadyExists(submission.task_id)
            if submission.canvas_id is not None:
                canvas = self._canvases.get(submission.account_space_id, submission.canvas_id)
                if canvas.user_id != submission.user_id:
                    raise CanvasNotFound(submission.canvas_id)
            active_units = sum(
                task.quantity
                for task in self._tasks_by_key.values()
                if task.account_space_id == submission.account_space_id and not task.status.is_terminal
            )
            if active_units + submission.quantity > self._max_active_tasks:
                from app.generation.models import GenerationConcurrencyLimit

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
                credit_freeze_id=freeze.freeze_id,
                model_price_version_id=freeze.model_price_version_id,
                frozen_credits=freeze.frozen_credits,
                status=GenerationTaskStatus.QUEUED,
                created_at=submission.submitted_at,
                updated_at=submission.submitted_at,
                reference_media_ids=submission.reference_media_ids,
                mask_media_id=submission.mask_media_id,
                selected_route_id=submission.selected_route_id,
                route_selection_reason=submission.route_selection_reason,
            )
            self._tasks_by_key[key] = task
            return task

    def get(self, account_space_id: str, task_id: str) -> GenerationTask:
        """读取账户空间拥有的任务。"""
        with self._lock:
            task = self._tasks_by_key.get((account_space_id, task_id))
        if task is None:
            raise GenerationTaskNotFound(task_id)
        return task

    def active_across_accounts(self) -> tuple[GenerationTask, ...]:
        """读取所有账户仍在排队或运行的任务。"""
        with self._lock:
            active = (task for task in self._tasks_by_key.values() if not task.status.is_terminal)
            return tuple(sorted(active, key=lambda task: (task.created_at, task.account_space_id, task.task_id)))

    def admin_recent(self, *, since: datetime | None, offset: int, limit: int) -> tuple[GenerationTask, ...]:
        with self._lock:
            values = [task for task in self._tasks_by_key.values() if since is None or task.created_at >= since]
            values.sort(key=lambda task: (task.created_at, task.task_id), reverse=True)
            return tuple(values[offset : offset + limit])

    def admin_total(self, *, since: datetime | None) -> int:
        with self._lock:
            return sum(1 for task in self._tasks_by_key.values() if since is None or task.created_at >= since)

    def expire_due(self, now: datetime) -> tuple[GenerationTask, ...]:
        """按当前管理员截止时间将活动任务失败并退款。"""
        deadline = self._deadline()
        with self._lock:
            due = tuple(
                sorted(
                    (
                        task
                        for task in self._tasks_by_key.values()
                        if generation_deadline_reached(task, at=now, deadline=deadline)
                    ),
                    key=lambda task: (task.created_at, task.account_space_id, task.task_id),
                )
            )
            return tuple(
                self.transition(
                    task.account_space_id,
                    task.task_id,
                    GenerationFailed(
                        reason=GENERATION_TIMEOUT_REASON,
                        outcome_reference=generation_timeout_reference(task),
                        occurred_at=now,
                    ),
                )
                for task in due
            )

    def transition(
        self,
        account_space_id: str,
        task_id: str,
        event: GenerationTransition,
    ) -> GenerationTask:
        """Apply a lifecycle event to an owned task."""
        with self._lock:
            task = self._tasks_by_key.get((account_space_id, task_id))
            if task is None:
                raise GenerationTaskNotFound(task_id)
            if task.status.is_terminal:
                if isinstance(event, GenerationSucceeded) and (
                    task.status is GenerationTaskStatus.SUCCEEDED
                    and task.delivered_quantity == event.delivered_quantity
                    and task.outcome_reference == event.outcome_reference
                ):
                    return task
                if isinstance(event, (GenerationFailed, GenerationCancelled)) and (
                    task.status
                    is (
                        GenerationTaskStatus.FAILED
                        if isinstance(event, GenerationFailed)
                        else GenerationTaskStatus.CANCELLED
                    )
                    and task.error == event.reason
                    and task.outcome_reference == event.outcome_reference
                ):
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
                self._tasks_by_key[(account_space_id, task_id)] = updated
                return updated
            if isinstance(event, GenerationStarted):
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
                self._tasks_by_key[(account_space_id, task_id)] = updated
                return updated
            if isinstance(event, GenerationSucceeded):
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
                self._tasks_by_key[(account_space_id, task_id)] = updated
                return updated
            if isinstance(event, (GenerationFailed, GenerationCancelled)):
                if task.status.is_terminal:
                    raise ValueError("terminal tasks cannot be released twice")
                self._credits.release(
                    task.credit_freeze_id,
                    release_reference=event.outcome_reference,
                    reason=event.reason,
                    occurred_at=event.occurred_at,
                )
                updated = replace(
                    task,
                    status=(
                        GenerationTaskStatus.FAILED
                        if isinstance(event, GenerationFailed)
                        else GenerationTaskStatus.CANCELLED
                    ),
                    error=event.reason,
                    outcome_reference=event.outcome_reference,
                    updated_at=event.occurred_at,
                )
                self._tasks_by_key[(account_space_id, task_id)] = updated
                return updated
            raise NotImplementedError(type(event).__name__)

    def active_for_canvas(self, account_space_id: str, canvas_id: str) -> tuple[GenerationTask, ...]:
        """按创建顺序读取指定画布的活动任务。"""
        with self._lock:
            return tuple(
                task
                for task in self._tasks_by_key.values()
                if task.account_space_id == account_space_id
                and task.canvas_id == canvas_id
                and not task.status.is_terminal
            )

    def recent_for_canvas(
        self,
        account_space_id: str,
        canvas_id: str,
        *,
        limit: int,
    ) -> tuple[GenerationTask, ...]:
        """按创建时间从新到旧读取指定画布最近任务，包含终态。"""
        if limit <= 0:
            raise ValueError("recent generation task limit must be positive")
        with self._lock:
            matching = (
                task
                for task in self._tasks_by_key.values()
                if task.account_space_id == account_space_id
                and task.canvas_id == canvas_id
                and (task.account_space_id, task.task_id) not in self._history_hidden_at_by_key
            )
            return tuple(sorted(matching, key=lambda task: (task.created_at, task.task_id), reverse=True)[:limit])

    def recent_for_account(
        self,
        account_space_id: str,
        *,
        limit: int,
    ) -> tuple[GenerationTask, ...]:
        """按创建时间从新到旧读取账户空间最近任务，包含终态。"""
        if limit <= 0:
            raise ValueError("recent generation task limit must be positive")
        with self._lock:
            matching = (
                task
                for task in self._tasks_by_key.values()
                if task.account_space_id == account_space_id
                and (task.account_space_id, task.task_id) not in self._history_hidden_at_by_key
            )
            return tuple(sorted(matching, key=lambda task: (task.created_at, task.task_id), reverse=True)[:limit])

    def clear_history(self, account_space_id: str, *, cleared_at: datetime) -> int:
        """Hide terminal tasks from user-facing history without deleting their facts."""
        with self._lock:
            keys = tuple(
                key
                for key, task in self._tasks_by_key.items()
                if task.account_space_id == account_space_id
                and task.status.is_terminal
                and key not in self._history_hidden_at_by_key
            )
            self._history_hidden_at_by_key.update({key: cleared_at for key in keys})
            return len(keys)

    def activity_summary(
        self,
        account_space_id: str,
        *,
        since: datetime | None,
    ) -> GenerationActivitySummary:
        with self._lock:
            tasks = tuple(
                task for task in self._tasks_by_key.values()
                if task.account_space_id == account_space_id and (since is None or task.created_at >= since)
            )
        succeeded = tuple(task for task in tasks if task.status is GenerationTaskStatus.SUCCEEDED)
        consumed_units = sum(
            int(Decimal(task.frozen_credits) * 10_000) * int(task.delivered_quantity or 0) // task.quantity
            for task in succeeded
        )
        return GenerationActivitySummary(
            total_tasks=len(tasks),
            succeeded_tasks=len(succeeded),
            failed_tasks=sum(task.status is GenerationTaskStatus.FAILED for task in tasks),
            consumed_credit_units=consumed_units,
        )


def _freeze_reference(submission: GenerationSubmission) -> str:
    return f"generation:{submission.account_space_id}:{submission.task_id}"


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
