"""生成尝试提交阶段的共享状态机。"""

from dataclasses import replace

from app.generation_attempts.models import (
    AttemptAccepted,
    AttemptRejected,
    AttemptSubmissionStarted,
    AttemptSubmissionUnknown,
    GenerationAttempt,
    GenerationAttemptConflict,
    GenerationAttemptStatus,
    GenerationAttemptTransition,
)


def transitioned(attempt: GenerationAttempt, event: GenerationAttemptTransition) -> GenerationAttempt:
    """应用一个幂等且不可改写历史的提交阶段事件。"""
    if isinstance(event, AttemptSubmissionStarted):
        if attempt.status is GenerationAttemptStatus.CREATED:
            return replace(
                attempt,
                status=GenerationAttemptStatus.SUBMITTING,
                submitted_at=event.occurred_at,
                updated_at=event.occurred_at,
            )
        if attempt.submitted_at == event.occurred_at:
            return attempt
    elif isinstance(event, AttemptAccepted):
        provider_task_id = event.provider_task_id.strip()
        if attempt.status in (GenerationAttemptStatus.SUBMITTING, GenerationAttemptStatus.UNKNOWN):
            return replace(
                attempt,
                status=GenerationAttemptStatus.PROVIDER_PENDING,
                provider_task_id=provider_task_id,
                error="",
                accepted_at=event.occurred_at,
                updated_at=event.occurred_at,
            )
        if (
            attempt.status is GenerationAttemptStatus.PROVIDER_PENDING
            and attempt.provider_task_id == provider_task_id
            and attempt.accepted_at == event.occurred_at
        ):
            return attempt
    elif isinstance(event, AttemptSubmissionUnknown):
        reason = _required(event.reason)
        if attempt.status is GenerationAttemptStatus.SUBMITTING:
            return replace(
                attempt,
                status=GenerationAttemptStatus.UNKNOWN,
                error=reason,
                updated_at=event.occurred_at,
            )
        if (
            attempt.status is GenerationAttemptStatus.UNKNOWN
            and attempt.error == reason
            and attempt.updated_at == event.occurred_at
        ):
            return attempt
    elif isinstance(event, AttemptRejected):
        error_code = _required(event.error_code)
        reason = _required(event.reason)
        if attempt.status in (GenerationAttemptStatus.SUBMITTING, GenerationAttemptStatus.UNKNOWN):
            return replace(
                attempt,
                status=GenerationAttemptStatus.FAILED,
                error_code=error_code,
                error=reason,
                finished_at=event.occurred_at,
                updated_at=event.occurred_at,
            )
        if (
            attempt.status is GenerationAttemptStatus.FAILED
            and attempt.error_code == error_code
            and attempt.error == reason
            and attempt.finished_at == event.occurred_at
        ):
            return attempt
    raise GenerationAttemptConflict(f"生成尝试 {attempt.attempt_id} 的状态转换冲突")


def _required(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise GenerationAttemptConflict("生成尝试结果原因不能为空")
    return normalized
