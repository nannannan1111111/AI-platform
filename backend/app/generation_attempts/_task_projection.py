"""将 Provider 受理事实投影到生成任务生命周期。"""

from datetime import datetime

from app.generation import GenerationStarted, GenerationTasks, GenerationTaskStatus, is_generation_timeout
from app.generation_attempts.models import GenerationAttempt, GenerationAttemptStatus


def start_task_if_provider_pending(
    generation_tasks: GenerationTasks,
    account_space_id: str,
    attempt: GenerationAttempt,
    *,
    occurred_at: datetime,
) -> None:
    """幂等补齐已受理生成尝试对应的任务运行状态。"""
    if attempt.status is not GenerationAttemptStatus.PROVIDER_PENDING:
        return
    task = generation_tasks.get(account_space_id, attempt.task_id)
    if task.status in {GenerationTaskStatus.QUEUED, GenerationTaskStatus.RUNNING} and not task.provider_task_id:
        generation_tasks.transition(
            account_space_id,
            attempt.task_id,
            GenerationStarted(
                provider_task_id=attempt.provider_task_id,
                occurred_at=attempt.accepted_at or occurred_at,
            ),
        )
        return
    # A provider may acknowledge work just after the deadline scheduler or an
    # administrator releases the reservation. Delivery discards those late
    # results, and this expected race is not an integrity conflict.
    if is_generation_timeout(task) or task.status is GenerationTaskStatus.CANCELLED:
        return
    if task.status is not GenerationTaskStatus.RUNNING or task.provider_task_id != attempt.provider_task_id:
        raise ValueError("provider acceptance conflicts with generation task state")
