"""状态未知生成尝试的主动核实行为。"""

import re
from collections.abc import Callable
from datetime import datetime

from app.generation import GenerationFailed, GenerationTasks
from app.generation_attempts._exhaustion import attempts_exhausted, fail_task_if_attempts_exhausted
from app.generation_attempts._provider import (
    ProviderGenerationResolutionRequest,
    ProviderGenerationResolutions,
    ProviderResolutionAccepted,
    ProviderResolutionRejected,
)
from app.generation_attempts._task_projection import start_task_if_provider_pending
from app.generation_attempts.interface import GenerationAttempts
from app.generation_attempts.models import (
    AttemptAccepted,
    AttemptRejected,
    GenerationAttempt,
    GenerationAttemptNotFound,
    GenerationAttemptStatus,
)

_SAFE_PROVIDER_ERROR_CODE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}").fullmatch


def _safe_provider_reason(value: str) -> str:
    reason = re.sub(r"https?://\S+", "<url>", value.strip(), flags=re.IGNORECASE)
    reason = re.sub(r"(?i)bearer\s+\S+", "Bearer <redacted>", reason)
    reason = re.sub(r"(?i)(?:api[_ -]?key|token|secret)[=: -]+\S+", "credential=<redacted>", reason)
    return re.sub(r"\s+", " ", reason)[:1024] or "provider explicitly rejected the request"


class GenerationAttemptReconciler:
    """核实未知上游提交，并在第二次明确失败后结束任务。"""

    def __init__(
        self,
        generation_attempts: GenerationAttempts,
        provider_resolutions: ProviderGenerationResolutions,
        *,
        generation_tasks: GenerationTasks,
        clock: Callable[[], datetime],
    ) -> None:
        self._generation_attempts = generation_attempts
        self._provider_resolutions = provider_resolutions
        self._generation_tasks = generation_tasks
        self._clock = clock

    def reconcile(self, account_space_id: str, task_id: str) -> GenerationAttempt:
        """核实任务当前未知尝试，不重提 Provider 请求。"""
        attempts = self._generation_attempts.for_task(account_space_id, task_id)
        if not attempts:
            raise GenerationAttemptNotFound(task_id)
        attempt = attempts[-1]
        if attempts_exhausted(attempt):
            fail_task_if_attempts_exhausted(
                self._generation_tasks,
                account_space_id,
                attempt,
                occurred_at=attempt.finished_at or self._clock(),
            )
            return attempt
        if attempt.status is not GenerationAttemptStatus.UNKNOWN:
            start_task_if_provider_pending(
                self._generation_tasks,
                account_space_id,
                attempt,
                occurred_at=self._clock(),
            )
            return attempt
        try:
            result = self._provider_resolutions.resolve(
                ProviderGenerationResolutionRequest(
                    route_id=attempt.route_id,
                    provider_idempotency_key=attempt.provider_idempotency_key,
                    provider_task_id=attempt.provider_task_id,
                )
            )
        except Exception:
            return attempt
        event: AttemptAccepted | AttemptRejected
        if not isinstance(result, ProviderResolutionAccepted):
            if not isinstance(result, ProviderResolutionRejected):
                return attempt
            error_code = result.error_code.strip()
            event = AttemptRejected(
                error_code=(error_code if _SAFE_PROVIDER_ERROR_CODE(error_code) is not None else "provider_rejected"),
                reason=_safe_provider_reason(result.reason),
                occurred_at=self._clock(),
            )
        else:
            provider_task_id = result.provider_task_id.strip()
            if not provider_task_id or len(provider_task_id) > 255:
                return attempt
            event = AttemptAccepted(provider_task_id=provider_task_id, occurred_at=self._clock())
        reconciled = self._generation_attempts.transition(account_space_id, attempt.attempt_id, event)
        if reconciled.status is GenerationAttemptStatus.FAILED:
            self._generation_tasks.transition(
                account_space_id,
                task_id,
                GenerationFailed(
                    reason=reconciled.error,
                    outcome_reference=f"generation-attempt:{reconciled.attempt_id}",
                    occurred_at=reconciled.finished_at or event.occurred_at,
                ),
            )
            return reconciled
        start_task_if_provider_pending(
            self._generation_tasks,
            account_space_id,
            reconciled,
            occurred_at=event.occurred_at,
        )
        fail_task_if_attempts_exhausted(
            self._generation_tasks,
            account_space_id,
            reconciled,
            occurred_at=event.occurred_at,
        )
        return reconciled
