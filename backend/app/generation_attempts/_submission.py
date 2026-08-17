"""生成尝试提交行为的实现。"""

import logging
import re
from collections.abc import Callable
from datetime import datetime
from threading import Lock

from app.generation import (
    GenerationDispatchStarted,
    GenerationFailed,
    GenerationTaskNotFound,
    GenerationTasks,
    GenerationTaskStatus,
)
from app.generation_attempts._exhaustion import attempts_exhausted, fail_task_if_attempts_exhausted
from app.generation_attempts._provider import (
    ProviderGenerationRequest,
    ProviderGenerationSubmissions,
    ProviderReferenceImage,
    ProviderSubmissionAccepted,
    ProviderSubmissionCompleted,
    ProviderSubmissionDeliveryFailed,
    ProviderSubmissionRejected,
    ProviderSubmissionUnknown,
)
from app.generation_attempts._task_projection import start_task_if_provider_pending
from app.generation_attempts.interface import GenerationAttempts
from app.generation_attempts.models import (
    AttemptAccepted,
    AttemptRejected,
    AttemptSubmissionStarted,
    AttemptSubmissionUnknown,
    GenerationAttempt,
    GenerationAttemptNotFound,
    GenerationAttemptPreparation,
    GenerationAttemptStatus,
)
from app.generation_results import GenerationImageContent, GenerationImageDelivery
from app.reference_media import ReferenceMedia

_SAFE_PROVIDER_ERROR_CODE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}").fullmatch
_IMAGE_DELIVERY_FAILED_REASON = "provider completed but image delivery failed"
_LOG = logging.getLogger(__name__)


def _safe_provider_reason(value: str) -> str:
    reason = re.sub(r"https?://\S+", "<url>", value.strip(), flags=re.IGNORECASE)
    reason = re.sub(r"(?i)bearer\s+\S+", "Bearer <redacted>", reason)
    reason = re.sub(r"(?i)(?:api[_ -]?key|token|secret)[=: -]+\S+", "credential=<redacted>", reason)
    return re.sub(r"\s+", " ", reason)[:1024] or "provider explicitly rejected the request"


class GenerationAttemptSubmitter:
    """以稳定身份向固化模型路由提交最多两次生成尝试。"""

    def __init__(
        self,
        generation_tasks: GenerationTasks,
        generation_attempts: GenerationAttempts,
        provider_submissions: ProviderGenerationSubmissions,
        *,
        clock: Callable[[], datetime],
        image_delivery: GenerationImageDelivery | None = None,
        reference_media: ReferenceMedia | None = None,
    ) -> None:
        self._generation_tasks = generation_tasks
        self._generation_attempts = generation_attempts
        self._provider_submissions = provider_submissions
        self._clock = clock
        self._image_delivery = image_delivery
        self._reference_media = reference_media

    def submit(self, account_space_id: str, task_id: str) -> GenerationAttempt:
        """提交当前尝试；第二次明确失败后结束任务并停止重试。"""
        try:
            task = self._generation_tasks.get(account_space_id, task_id)
        except GenerationTaskNotFound as exc:
            raise GenerationAttemptNotFound(task_id) from exc
        occurred_at = self._clock()
        existing_attempts = self._generation_attempts.for_task(account_space_id, task_id)
        if task.status.is_terminal and existing_attempts:
            return existing_attempts[-1]
        if existing_attempts and attempts_exhausted(existing_attempts[-1]):
            exhausted = existing_attempts[-1]
            fail_task_if_attempts_exhausted(
                self._generation_tasks,
                account_space_id,
                exhausted,
                occurred_at=occurred_at,
            )
            return exhausted
        attempt = self._generation_attempts.prepare(
            GenerationAttemptPreparation(
                account_space_id=account_space_id,
                task_id=task_id,
                route_id=task.selected_route_id,
                occurred_at=occurred_at,
            )
        )
        if attempt.status is GenerationAttemptStatus.SUBMITTING:
            self._generation_tasks.transition(
                account_space_id,
                task_id,
                GenerationDispatchStarted(occurred_at=occurred_at),
            )
            return self._generation_attempts.transition(
                account_space_id,
                attempt.attempt_id,
                AttemptSubmissionUnknown(
                    reason="provider submission was interrupted before its outcome was recorded",
                    occurred_at=occurred_at,
                ),
            )
        if attempt.status is not GenerationAttemptStatus.CREATED:
            if attempt.status is GenerationAttemptStatus.UNKNOWN and task.status is GenerationTaskStatus.QUEUED:
                self._generation_tasks.transition(
                    account_space_id,
                    task_id,
                    GenerationDispatchStarted(occurred_at=occurred_at),
                )
            start_task_if_provider_pending(
                self._generation_tasks,
                account_space_id,
                attempt,
                occurred_at=occurred_at,
            )
            return attempt
        submitting = self._generation_attempts.transition(
            account_space_id,
            attempt.attempt_id,
            AttemptSubmissionStarted(occurred_at=occurred_at),
        )
        self._generation_tasks.transition(
            account_space_id,
            task_id,
            GenerationDispatchStarted(occurred_at=occurred_at),
        )
        image_delivery = self._image_delivery
        completed_images: tuple[GenerationImageContent, ...] | None = None
        provider_delivery_failed = False
        streamed_attempt: GenerationAttempt | None = None
        streamed_delivery_lock = Lock()
        result: (
            ProviderSubmissionAccepted
            | ProviderSubmissionCompleted
            | ProviderSubmissionDeliveryFailed
            | ProviderSubmissionRejected
            | ProviderSubmissionUnknown
            | None
        ) = None
        try:
            reference_images: tuple[ProviderReferenceImage, ...] = ()
            mask: ProviderReferenceImage | None = None
            if task.reference_media_ids or task.mask_media_id:
                reference_media = self._reference_media
                if reference_media is None:
                    raise RuntimeError("reference media is not configured")

                def provider_image(media_id: str) -> ProviderReferenceImage:
                    available = reference_media.read(
                        account_space_id,
                        media_id,
                        at=occurred_at,
                    )
                    return ProviderReferenceImage(
                        filename=available.media.original_name,
                        mime_type=available.media.mime_type,
                        content=available.content,
                    )

                reference_images = tuple(provider_image(media_id) for media_id in task.reference_media_ids)
                if task.mask_media_id:
                    mask = provider_image(task.mask_media_id)

            def task_should_continue() -> bool:
                checked_at = self._clock()
                self._generation_tasks.expire_due(checked_at)
                return not self._generation_tasks.get(account_space_id, task_id).status.is_terminal

            def receive_streamed_image(image: GenerationImageContent) -> None:
                nonlocal streamed_attempt
                if image_delivery is None:
                    raise RuntimeError("streamed provider images require image delivery")
                with streamed_delivery_lock:
                    image_completed_at = self._clock()
                    if streamed_attempt is None:
                        streamed_attempt = self._generation_attempts.transition(
                            account_space_id,
                            submitting.attempt_id,
                            AttemptAccepted(
                                provider_task_id=f"direct:{submitting.provider_idempotency_key}",
                                occurred_at=image_completed_at,
                            ),
                        )
                        start_task_if_provider_pending(
                            self._generation_tasks,
                            account_space_id,
                            streamed_attempt,
                            occurred_at=image_completed_at,
                        )
                    if not task_should_continue():
                        raise RuntimeError("generation task is no longer active")
                    image_delivery.receive_partial(
                        account_space_id,
                        task_id,
                        (image,),
                        completed_at=image_completed_at,
                    )

            result = self._provider_submissions.submit(
                ProviderGenerationRequest(
                    route_id=submitting.route_id,
                    provider_idempotency_key=submitting.provider_idempotency_key,
                    prompt=task.prompt,
                    aspect_ratio=task.params.aspect_ratio,
                    quantity=task.quantity,
                    output_spec=task.output_spec,
                    quality=task.params.quality,
                    size=task.params.size,
                    resolution_tier=task.params.resolution_tier,
                    output_format=task.params.output_format,
                    operation=task.params.operation,
                    input_fidelity=task.params.input_fidelity,
                    reference_images=reference_images,
                    mask=mask,
                    on_image=receive_streamed_image if image_delivery is not None else None,
                    should_continue=task_should_continue,
                )
            )
            if isinstance(result, ProviderSubmissionCompleted):
                if image_delivery is None:
                    raise RuntimeError("completed provider submissions require image delivery")
                if streamed_attempt is None:
                    completed_images = result.images
            elif isinstance(result, ProviderSubmissionDeliveryFailed):
                provider_delivery_failed = True
            completed_at = self._clock()
            event = self._event_for(result, completed_at)
        except Exception:
            completed_at = self._clock()
            provider_delivery_failed = streamed_attempt is not None
            event = AttemptSubmissionUnknown(
                reason="provider submission status is unknown",
                occurred_at=completed_at,
            )
        completed = streamed_attempt or self._generation_attempts.transition(
            account_space_id,
            submitting.attempt_id,
            event,
        )
        if completed.status is GenerationAttemptStatus.FAILED:
            self._generation_tasks.transition(
                account_space_id,
                task_id,
                GenerationFailed(
                    reason=completed.error,
                    outcome_reference=f"generation-attempt:{completed.attempt_id}",
                    occurred_at=completed.finished_at or completed_at,
                ),
            )
            return completed
        self._generation_tasks.expire_due(completed_at)
        current_task = self._generation_tasks.get(account_space_id, task_id)
        if current_task.status.is_terminal:
            return completed
        start_task_if_provider_pending(
            self._generation_tasks,
            account_space_id,
            completed,
            occurred_at=completed_at,
        )
        if provider_delivery_failed:
            delivery_failure_reason = (
                _safe_provider_reason(result.reason)
                if isinstance(result, ProviderSubmissionDeliveryFailed)
                else _IMAGE_DELIVERY_FAILED_REASON
            )
            _LOG.error(
                "provider completed but image result was not deliverable account_space_id=%s task_id=%s "
                "attempt_id=%s reason=%s",
                account_space_id,
                task_id,
                completed.attempt_id,
                delivery_failure_reason,
            )
            self._generation_tasks.transition(
                account_space_id,
                task_id,
                GenerationFailed(
                    reason=delivery_failure_reason,
                    outcome_reference=f"generation-delivery:{completed.attempt_id}",
                    occurred_at=completed_at,
                ),
            )
            return completed
        if completed_images is not None:
            assert image_delivery is not None
            try:
                image_delivery.receive(
                    account_space_id,
                    task_id,
                    completed_images,
                    completed_at=completed_at,
                )
            except Exception as exc:
                _LOG.exception(
                    "completed image delivery failed account_space_id=%s task_id=%s attempt_id=%s error_type=%s",
                    account_space_id,
                    task_id,
                    completed.attempt_id,
                    type(exc).__name__,
                )
                failed_at = self._clock()
                current_task = self._generation_tasks.get(account_space_id, task_id)
                if not current_task.status.is_terminal:
                    self._generation_tasks.transition(
                        account_space_id,
                        task_id,
                        GenerationFailed(
                            reason=_IMAGE_DELIVERY_FAILED_REASON,
                            outcome_reference=f"generation-delivery:{completed.attempt_id}",
                            occurred_at=failed_at,
                        ),
                    )
                return completed
        elif streamed_attempt is not None and isinstance(result, ProviderSubmissionCompleted):
            assert image_delivery is not None
            try:
                image_delivery.finalize(account_space_id, task_id, completed_at=completed_at)
            except Exception as exc:
                _LOG.exception(
                    "streamed image finalization failed account_space_id=%s task_id=%s attempt_id=%s error_type=%s",
                    account_space_id,
                    task_id,
                    completed.attempt_id,
                    type(exc).__name__,
                )
                failed_at = self._clock()
                current_task = self._generation_tasks.get(account_space_id, task_id)
                if not current_task.status.is_terminal:
                    self._generation_tasks.transition(
                        account_space_id,
                        task_id,
                        GenerationFailed(
                            reason=_IMAGE_DELIVERY_FAILED_REASON,
                            outcome_reference=f"generation-delivery:{completed.attempt_id}",
                            occurred_at=failed_at,
                        ),
                    )
                return completed
        fail_task_if_attempts_exhausted(
            self._generation_tasks,
            account_space_id,
            completed,
            occurred_at=completed_at,
        )
        return completed

    @staticmethod
    def _event_for(
        result: (
            ProviderSubmissionAccepted
            | ProviderSubmissionCompleted
            | ProviderSubmissionDeliveryFailed
            | ProviderSubmissionRejected
            | ProviderSubmissionUnknown
        ),
        occurred_at: datetime,
    ) -> AttemptAccepted | AttemptRejected | AttemptSubmissionUnknown:
        if isinstance(
            result,
            (ProviderSubmissionAccepted, ProviderSubmissionCompleted, ProviderSubmissionDeliveryFailed),
        ):
            provider_task_id = result.provider_task_id.strip()
            if not provider_task_id or len(provider_task_id) > 255:
                raise ValueError("invalid provider task id")
            return AttemptAccepted(provider_task_id=provider_task_id, occurred_at=occurred_at)
        if isinstance(result, ProviderSubmissionRejected):
            error_code = result.error_code.strip()
            return AttemptRejected(
                error_code=(error_code if _SAFE_PROVIDER_ERROR_CODE(error_code) is not None else "provider_rejected"),
                reason=_safe_provider_reason(result.reason),
                occurred_at=occurred_at,
            )
        if isinstance(result, ProviderSubmissionUnknown):
            return AttemptSubmissionUnknown(
                reason="provider submission status is unknown",
                occurred_at=occurred_at,
            )
        raise TypeError(type(result).__name__)
