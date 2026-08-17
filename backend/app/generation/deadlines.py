"""生成任务的服务端权威截止规则。"""

from datetime import datetime, timedelta

from app.generation.models import GenerationTask

# Durable queue wait time is intentionally excluded. The window begins only
# after a worker has acquired the user's execution slot and is about to send
# the first paid Provider request.
GENERATION_TASK_DEADLINE = timedelta(minutes=10)
GENERATION_TIMEOUT_REASON = "generation task exceeded configured deadline"
_LEGACY_GENERATION_TIMEOUT_REASON = "generation task exceeded twenty minute deadline"


def generation_timeout_reference(task: GenerationTask) -> str:
    """返回任务超时失败使用的稳定幂等引用。"""
    return f"generation-timeout:{task.account_space_id}:{task.task_id}"


def generation_deadline_reached(
    task: GenerationTask,
    *,
    at: datetime,
    deadline: timedelta = GENERATION_TASK_DEADLINE,
) -> bool:
    """判断已开始向上游提交的活动任务是否到达指定截止点。"""
    return (
        not task.status.is_terminal
        and task.started_at is not None
        and task.started_at + deadline <= at
    )


def is_generation_timeout(task: GenerationTask) -> bool:
    """判断终态任务是否由权威截止规则失败。"""
    return task.error in {
        GENERATION_TIMEOUT_REASON,
        _LEGACY_GENERATION_TIMEOUT_REASON,
    } and task.outcome_reference == generation_timeout_reference(task)
