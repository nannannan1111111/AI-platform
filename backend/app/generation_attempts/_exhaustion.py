"""生成尝试耗尽后的任务收口规则。"""

from datetime import datetime

from app.generation import GenerationFailed, GenerationTasks
from app.generation_attempts.models import GenerationAttempt, GenerationAttemptStatus

MAX_GENERATION_ATTEMPTS = 2
_EXHAUSTED_REASON = "generation attempts exhausted"


def attempts_exhausted(attempt: GenerationAttempt) -> bool:
    """判断最新尝试是否已经用尽任务的提交次数。"""
    return attempt.status is GenerationAttemptStatus.FAILED and attempt.attempt_no >= MAX_GENERATION_ATTEMPTS


def fail_task_if_attempts_exhausted(
    generation_tasks: GenerationTasks,
    account_space_id: str,
    attempt: GenerationAttempt,
    *,
    occurred_at: datetime,
) -> None:
    """幂等结束已耗尽尝试的任务并释放其冻结额度。"""
    if not attempts_exhausted(attempt):
        return
    generation_tasks.transition(
        account_space_id,
        attempt.task_id,
        GenerationFailed(
            reason=_EXHAUSTED_REASON,
            outcome_reference=f"generation-attempt:{attempt.attempt_id}",
            occurred_at=attempt.finished_at or occurred_at,
        ),
    )
