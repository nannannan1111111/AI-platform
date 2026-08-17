import base64
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.canvases import CanvasCreation, InMemoryCanvases
from app.credits import InMemoryCredits, InMemoryModelPrices
from app.generation import GenerationParameters, GenerationSubmission, GenerationTaskStatus, InMemoryGenerationTasks
from app.generation_attempts import (
    AttemptAccepted,
    AttemptRejected,
    AttemptSubmissionStarted,
    AttemptSubmissionUnknown,
    GenerationAttemptConflict,
    GenerationAttemptNotFound,
    GenerationAttemptPreparation,
    GenerationAttemptReconciler,
    GenerationAttemptStatus,
    GenerationAttemptSubmitter,
    InMemoryGenerationAttempts,
)
from app.generation_attempts._provider import (
    ProviderGenerationRequest,
    ProviderGenerationResolutionRequest,
    ProviderResolutionAccepted,
    ProviderResolutionRejected,
    ProviderResolutionUnknown,
    ProviderSubmissionAccepted,
    ProviderSubmissionCompleted,
    ProviderSubmissionDeliveryFailed,
    ProviderSubmissionRejected,
    ProviderSubmissionUnknown,
)
from app.generation_results import GenerationImageContent, GenerationImageDelivery
from app.media import FileSystemMediaObjects, InMemoryGeneratedMedia, InMemoryStorageAllowances
from app.provider_costs import InMemoryProviderCostRates, ProviderCostRateNotFound
from app.reference_media import InMemoryReferenceMedia, ReferenceMediaUpload

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class AcceptingProviderSubmissions:
    def __init__(self, *, on_submit: Callable[[], None] = lambda: None) -> None:
        self.requests: list[ProviderGenerationRequest] = []
        self._on_submit = on_submit

    def submit(self, request: ProviderGenerationRequest) -> ProviderSubmissionAccepted:
        self._on_submit()
        self.requests.append(request)
        return ProviderSubmissionAccepted(provider_task_id="provider-task-1")


class RejectingProviderSubmissions:
    def submit(self, request: ProviderGenerationRequest) -> ProviderSubmissionRejected:
        return ProviderSubmissionRejected(
            error_code="quota_exceeded",
            reason="request rejected with api_key=provider-secret",
        )


class UnknownProviderSubmissions:
    def submit(self, request: ProviderGenerationRequest) -> ProviderSubmissionUnknown:
        return ProviderSubmissionUnknown(reason="timeout while using api_key=provider-secret")


class FailingProviderSubmissions:
    def submit(self, request: ProviderGenerationRequest) -> ProviderSubmissionAccepted:
        raise RuntimeError("network failure with api_key=provider-secret")


class InvalidProviderSubmissions:
    def submit(self, request: ProviderGenerationRequest) -> object:
        return {"api_key": "provider-secret", "unexpected": True}


class MissingProviderTaskIdSubmissions:
    def submit(self, request: ProviderGenerationRequest) -> ProviderSubmissionAccepted:
        return ProviderSubmissionAccepted(provider_task_id="  ")


class AcceptingProviderResolutions:
    def __init__(self) -> None:
        self.requests: list[ProviderGenerationResolutionRequest] = []

    def resolve(self, request: ProviderGenerationResolutionRequest) -> ProviderResolutionAccepted:
        self.requests.append(request)
        return ProviderResolutionAccepted(provider_task_id="provider-task-1")


class RejectingProviderResolutions:
    def resolve(self, request: ProviderGenerationResolutionRequest) -> ProviderResolutionRejected:
        return ProviderResolutionRejected(
            error_code="not_accepted",
            reason="provider lookup used api_key=provider-secret",
        )


class UnknownProviderResolutions:
    def resolve(self, request: ProviderGenerationResolutionRequest) -> ProviderResolutionUnknown:
        return ProviderResolutionUnknown(reason="lookup timed out with api_key=provider-secret")


class FailingProviderResolutions:
    def resolve(self, request: ProviderGenerationResolutionRequest) -> ProviderResolutionAccepted:
        raise RuntimeError("lookup failed with api_key=provider-secret")


def test_generation_attempt_freezes_the_route_current_cost_entered_in_cents() -> None:
    tasks, _credits, now = _funded_tasks()
    tasks.submit(
        GenerationSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-current-cost",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="测试当前成本",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
            selected_route_id="route-1",
            route_selection_reason="automatic",
        )
    )
    costs = InMemoryProviderCostRates(id_factory=lambda: "cost-rate-current", clock=lambda: now)
    costs.replace("route-1", provider_currency="USD", cost_per_image_cents=12)
    attempts = InMemoryGenerationAttempts(tasks, provider_cost_rates=costs, id_factory=lambda: "attempt-1")

    prepared = attempts.prepare(
        GenerationAttemptPreparation(
            account_space_id="account-space-1",
            task_id="task-current-cost",
            route_id="route-1",
            occurred_at=now,
        )
    )

    assert prepared.provider_cost_rate_id == "cost-rate-current"


def test_submitting_a_generation_attempt_records_explicit_provider_acceptance() -> None:
    attempts, tasks, credits, now = _attempt_dependencies()

    def assert_submitting_was_persisted() -> None:
        current = attempts.for_task("account-space-1", "task-1")
        assert len(current) == 1
        assert current[0].status is GenerationAttemptStatus.SUBMITTING

    provider = AcceptingProviderSubmissions(on_submit=assert_submitting_was_persisted)
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        provider,
        clock=lambda: now + timedelta(seconds=1),
    )

    submitted = submitter.submit("account-space-1", "task-1")

    assert submitted.status is GenerationAttemptStatus.PROVIDER_PENDING
    assert submitted.provider_task_id == "provider-task-1"
    assert provider.requests[0].should_continue is not None
    assert [replace(provider.requests[0], should_continue=None)] == [
        ProviderGenerationRequest(
            route_id="route-1",
            provider_idempotency_key=submitted.provider_idempotency_key,
            prompt="测试生成请求",
            aspect_ratio="1:1",
            quantity=1,
            output_spec="4k",
        )
    ]
    assert attempts.for_task("account-space-1", "task-1") == (submitted,)
    running_task = tasks.get("account-space-1", "task-1")
    assert running_task.status is GenerationTaskStatus.RUNNING
    assert running_task.provider_task_id == submitted.provider_task_id
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def test_submitting_an_edit_attempt_reads_account_checked_reference_image_and_mask() -> None:
    tasks, _credits, now = _funded_tasks()
    references = InMemoryReferenceMedia(
        id_factory=iter(("reference-1", "mask-1")).__next__,
    )
    uploaded = references.upload(
        ReferenceMediaUpload(
            user_id="user-1",
            account_space_id="account-space-1",
            original_name="composition.png",
            declared_mime_type="image/png",
            content=_PNG_BYTES,
            created_at=now,
        )
    )
    mask = references.upload(
        ReferenceMediaUpload(
            user_id="user-1",
            account_space_id="account-space-1",
            original_name="selection.png",
            declared_mime_type="image/png",
            content=_PNG_BYTES,
            created_at=now,
        )
    )
    tasks.submit(
        GenerationSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id=None,
            task_id="edit-task-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="Keep this composition",
            params=GenerationParameters(
                aspect_ratio="1:1",
                quality="high",
                size="2048x2048",
            ),
            submitted_at=now,
            reference_media_ids=(uploaded.media_id,),
            mask_media_id=mask.media_id,
            selected_route_id="route-1",
            route_selection_reason="automatic",
        )
    )
    attempts = InMemoryGenerationAttempts(
        tasks,
        provider_cost_rates=_provider_costs(now),
        id_factory=iter(("attempt-edit-1", "attempt-edit-2")).__next__,
    )
    provider = AcceptingProviderSubmissions()
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        provider,
        reference_media=references,
        clock=lambda: now + timedelta(seconds=1),
    )

    submitted = submitter.submit("account-space-1", "edit-task-1")

    request = provider.requests[0]
    assert request.quality == "high"
    assert request.size == "2048x2048"
    assert request.resolution_tier == ""
    assert request.reference_images[0].filename == "composition.png"
    assert request.reference_images[0].mime_type == "image/png"
    assert request.reference_images[0].content == _PNG_BYTES
    assert request.mask is not None
    assert request.mask.filename == "selection.png"
    assert request.mask.mime_type == "image/png"
    assert request.mask.content == _PNG_BYTES
    assert submitted.status is GenerationAttemptStatus.PROVIDER_PENDING


def test_completed_provider_submission_delivers_media_and_settles_the_task(tmp_path: Path) -> None:
    attempts, tasks, credits, now = _attempt_dependencies()

    class CompletedProviderSubmissions:
        def submit(self, request: ProviderGenerationRequest) -> ProviderSubmissionCompleted:
            return ProviderSubmissionCompleted(
                provider_task_id=f"direct:{request.provider_idempotency_key}",
                images=(
                    GenerationImageContent(
                        result_reference=f"{request.provider_idempotency_key}:1",
                        mime_type="image/png",
                        content=_PNG_BYTES,
                    ),
                ),
            )

    objects = FileSystemMediaObjects(tmp_path / "generated-media")
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=objects,
        storage_allowances=InMemoryStorageAllowances({}),
        id_factory=lambda: "media-1",
    )
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        CompletedProviderSubmissions(),
        image_delivery=GenerationImageDelivery(tasks, media, objects),
        clock=lambda: now + timedelta(seconds=1),
    )

    submitted = submitter.submit("account-space-1", "task-1")

    task = tasks.get("account-space-1", "task-1")
    registered = media.list_for_task("account-space-1", "task-1")
    assert submitted.status is GenerationAttemptStatus.PROVIDER_PENDING
    assert task.status is GenerationTaskStatus.SUCCEEDED
    assert task.delivered_quantity == 1
    assert tuple(item.media_id for item in registered) == ("media-1",)
    assert objects.read(registered[0].object_key) == _PNG_BYTES
    assert tuple(entry.kind for entry in credits.statement("account-space-1").entries).count("settlement") == 1


def test_streamed_provider_submission_registers_each_image_before_final_settlement(tmp_path: Path) -> None:
    tasks, credits, now = _funded_tasks()
    tasks.submit(
        GenerationSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-streamed",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=2,
            prompt="two streamed images",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
            selected_route_id="route-1",
            route_selection_reason="automatic",
        )
    )
    attempts = InMemoryGenerationAttempts(
        tasks,
        provider_cost_rates=_provider_costs(now),
        id_factory=lambda: "attempt-streamed",
    )
    objects = FileSystemMediaObjects(tmp_path / "generated-media")
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=objects,
        storage_allowances=InMemoryStorageAllowances({}),
        id_factory=iter(("media-1", "media-2")).__next__,
    )
    observed_counts: list[int] = []

    class StreamingProviderSubmissions:
        def submit(self, request: ProviderGenerationRequest) -> ProviderSubmissionCompleted:
            assert request.on_image is not None
            images = tuple(
                GenerationImageContent(
                    result_reference=f"{request.provider_idempotency_key}:{index}",
                    mime_type="image/png",
                    content=_PNG_BYTES + bytes((index,)),
                )
                for index in (1, 2)
            )
            for image in images:
                request.on_image(image)
                observed_counts.append(len(media.list_for_task("account-space-1", "task-streamed")))
                assert tasks.get("account-space-1", "task-streamed").status is GenerationTaskStatus.RUNNING
            return ProviderSubmissionCompleted(
                provider_task_id=f"direct:{request.provider_idempotency_key}",
                images=images,
            )

    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        StreamingProviderSubmissions(),
        image_delivery=GenerationImageDelivery(tasks, media, objects),
        clock=lambda: now + timedelta(seconds=1),
    )

    submitted = submitter.submit("account-space-1", "task-streamed")

    task = tasks.get("account-space-1", "task-streamed")
    assert observed_counts == [1, 2]
    assert submitted.status is GenerationAttemptStatus.PROVIDER_PENDING
    assert task.status is GenerationTaskStatus.SUCCEEDED
    assert task.delivered_quantity == 2
    assert credits.statement("account-space-1").frozen_credits == "0.0000"


def test_completed_provider_delivery_failure_fails_refunds_and_never_resubmits() -> None:
    attempts, tasks, credits, now = _attempt_dependencies()

    class CompletedProviderSubmissions:
        def __init__(self) -> None:
            self.requests: list[ProviderGenerationRequest] = []

        def submit(self, request: ProviderGenerationRequest) -> ProviderSubmissionCompleted:
            self.requests.append(request)
            return ProviderSubmissionCompleted(
                provider_task_id=f"direct:{request.provider_idempotency_key}",
                images=(
                    GenerationImageContent(
                        result_reference=f"{request.provider_idempotency_key}:1",
                        mime_type="image/png",
                        content=_PNG_BYTES,
                    ),
                ),
            )

    class FailingImageDelivery:
        def receive(self, *args: object, **kwargs: object) -> None:
            raise OSError("storage-secret should only appear in logs")

    provider = CompletedProviderSubmissions()
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        provider,
        image_delivery=FailingImageDelivery(),  # type: ignore[arg-type]
        clock=lambda: now + timedelta(seconds=1),
    )

    submitted = submitter.submit("account-space-1", "task-1")
    replay = submitter.submit("account-space-1", "task-1")

    task = tasks.get("account-space-1", "task-1")
    assert replay == submitted
    assert len(provider.requests) == 1
    assert task.status is GenerationTaskStatus.FAILED
    assert task.error == "provider completed but image delivery failed"
    assert task.outcome_reference == f"generation-delivery:{submitted.attempt_id}"
    assert "storage-secret" not in repr(task)
    assert credits.statement("account-space-1").available_credits == "1.0000"
    assert credits.statement("account-space-1").frozen_credits == "0.0000"


def test_provider_completed_but_result_download_failed_is_terminal_and_refunded() -> None:
    attempts, tasks, credits, now = _attempt_dependencies()

    class UndeliverableProviderSubmission:
        def __init__(self) -> None:
            self.requests: list[ProviderGenerationRequest] = []

        def submit(self, request: ProviderGenerationRequest) -> ProviderSubmissionDeliveryFailed:
            self.requests.append(request)
            return ProviderSubmissionDeliveryFailed(
                provider_task_id="request:9cd5e26f-69b5-4e18-abfd-9af3e708b5a0",
                reason=(
                    "上游已受理请求但未交付图片：Upstream request failed；"
                    "上游请求 ID：9cd5e26f-69b5-4e18-abfd-9af3e708b5a0"
                ),
            )

    provider = UndeliverableProviderSubmission()
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        provider,
        clock=lambda: now + timedelta(seconds=1),
    )

    submitted = submitter.submit("account-space-1", "task-1")
    replay = submitter.submit("account-space-1", "task-1")

    task = tasks.get("account-space-1", "task-1")
    assert submitted.status is GenerationAttemptStatus.PROVIDER_PENDING
    assert submitted.provider_task_id == "request:9cd5e26f-69b5-4e18-abfd-9af3e708b5a0"
    assert replay == submitted
    assert len(provider.requests) == 1
    assert task.status is GenerationTaskStatus.FAILED
    assert task.error == (
        "上游已受理请求但未交付图片：Upstream request failed；"
        "上游请求 ID：9cd5e26f-69b5-4e18-abfd-9af3e708b5a0"
    )
    assert credits.statement("account-space-1").available_credits == "1.0000"
    assert credits.statement("account-space-1").frozen_credits == "0.0000"


def test_completed_provider_submission_after_twenty_minutes_is_discarded_and_refunded(tmp_path: Path) -> None:
    attempts, tasks, credits, now = _attempt_dependencies()
    current_time = [now + timedelta(seconds=1)]

    class LateCompletedProviderSubmissions:
        def submit(self, request: ProviderGenerationRequest) -> ProviderSubmissionCompleted:
            current_time[0] = now + timedelta(minutes=20, seconds=1)
            return ProviderSubmissionCompleted(
                provider_task_id=f"direct:{request.provider_idempotency_key}",
                images=(
                    GenerationImageContent(
                        result_reference=f"{request.provider_idempotency_key}:1",
                        mime_type="image/png",
                        content=_PNG_BYTES,
                    ),
                ),
            )

    objects = FileSystemMediaObjects(tmp_path / "generated-media")
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=objects,
        storage_allowances=InMemoryStorageAllowances({}),
        id_factory=lambda: "media-1",
    )
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        LateCompletedProviderSubmissions(),
        image_delivery=GenerationImageDelivery(tasks, media, objects),
        clock=lambda: current_time[0],
    )

    submitted = submitter.submit("account-space-1", "task-1")

    task = tasks.get("account-space-1", "task-1")
    assert submitted.status is GenerationAttemptStatus.PROVIDER_PENDING
    assert task.status is GenerationTaskStatus.FAILED
    assert task.error == "generation task exceeded configured deadline"
    assert credits.statement("account-space-1").available_credits == "1.0000"
    assert credits.statement("account-space-1").frozen_credits == "0.0000"
    assert media.list_for_task("account-space-1", "task-1") == ()
    assert not (tmp_path / "generated-media").exists()


def test_provider_can_stop_work_as_soon_as_the_task_deadline_is_reached() -> None:
    attempts, tasks, credits, now = _attempt_dependencies()
    current_time = [now + timedelta(seconds=1)]

    class DeadlineAwareProviderSubmissions:
        def submit(self, request: ProviderGenerationRequest) -> ProviderSubmissionDeliveryFailed:
            assert request.should_continue is not None
            assert request.should_continue() is True
            current_time[0] = now + timedelta(minutes=20, seconds=1)
            assert request.should_continue() is False
            return ProviderSubmissionDeliveryFailed(
                provider_task_id=f"direct:{request.provider_idempotency_key}",
                reason="generation task stopped at its deadline",
            )

    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        DeadlineAwareProviderSubmissions(),
        clock=lambda: current_time[0],
    )

    submitted = submitter.submit("account-space-1", "task-1")

    task = tasks.get("account-space-1", "task-1")
    assert submitted.status is GenerationAttemptStatus.PROVIDER_PENDING
    assert task.status is GenerationTaskStatus.FAILED
    assert task.error == "generation task exceeded configured deadline"
    assert credits.statement("account-space-1").frozen_credits == "0.0000"


def test_late_streamed_image_keeps_the_authoritative_timeout_instead_of_acceptance_conflict(tmp_path: Path) -> None:
    attempts, tasks, credits, now = _attempt_dependencies()
    current_time = [now + timedelta(seconds=1)]

    class LateStreamingProviderSubmissions:
        def submit(self, request: ProviderGenerationRequest) -> ProviderSubmissionCompleted:
            assert request.on_image is not None
            current_time[0] = now + timedelta(minutes=20, seconds=1)
            image = GenerationImageContent(
                result_reference=f"{request.provider_idempotency_key}:1",
                mime_type="image/png",
                content=_PNG_BYTES,
            )
            request.on_image(image)
            return ProviderSubmissionCompleted(
                provider_task_id=f"direct:{request.provider_idempotency_key}",
                images=(image,),
            )

    objects = FileSystemMediaObjects(tmp_path / "generated-media")
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=objects,
        storage_allowances=InMemoryStorageAllowances({}),
        id_factory=lambda: "media-1",
    )
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        LateStreamingProviderSubmissions(),
        image_delivery=GenerationImageDelivery(tasks, media, objects),
        clock=lambda: current_time[0],
    )

    submitted = submitter.submit("account-space-1", "task-1")

    task = tasks.get("account-space-1", "task-1")
    assert submitted.status is GenerationAttemptStatus.PROVIDER_PENDING
    assert task.status is GenerationTaskStatus.FAILED
    assert task.error == "generation task exceeded configured deadline"
    assert credits.statement("account-space-1").available_credits == "1.0000"
    assert media.list_for_task("account-space-1", "task-1") == ()


def test_reconciling_an_unknown_attempt_records_confirmed_provider_acceptance() -> None:
    attempts, tasks, credits, prepared, now = _prepared_attempt()
    attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptSubmissionStarted(occurred_at=now + timedelta(seconds=1)),
    )
    unknown = attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptSubmissionUnknown(
            reason="provider submission status is unknown",
            occurred_at=now + timedelta(seconds=2),
        ),
    )
    provider = AcceptingProviderResolutions()
    reconciler = GenerationAttemptReconciler(
        attempts,
        provider,
        generation_tasks=tasks,
        clock=lambda: now + timedelta(seconds=3),
    )

    reconciled = reconciler.reconcile("account-space-1", "task-1")

    assert reconciled.status is GenerationAttemptStatus.PROVIDER_PENDING
    assert reconciled.attempt_id == unknown.attempt_id
    assert reconciled.route_id == unknown.route_id
    assert reconciled.provider_idempotency_key == unknown.provider_idempotency_key
    assert reconciled.provider_cost_rate_id == unknown.provider_cost_rate_id
    assert reconciled.provider_task_id == "provider-task-1"
    assert provider.requests == [
        ProviderGenerationResolutionRequest(
            route_id=unknown.route_id,
            provider_idempotency_key=unknown.provider_idempotency_key,
            provider_task_id="",
        )
    ]
    assert attempts.for_task("account-space-1", "task-1") == (reconciled,)
    running_task = tasks.get("account-space-1", "task-1")
    assert running_task.status is GenerationTaskStatus.RUNNING
    assert running_task.provider_task_id == reconciled.provider_task_id
    assert credits.statement("account-space-1").frozen_credits == "0.1500"

    replay = reconciler.reconcile("account-space-1", "task-1")
    assert replay == reconciled
    assert len(provider.requests) == 1


def test_reconciling_provider_pending_attempt_starts_queued_task_without_provider_lookup() -> None:
    attempts, tasks, _, prepared, now = _prepared_attempt()
    attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptSubmissionStarted(occurred_at=now + timedelta(seconds=1)),
    )
    existing = attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptAccepted(
            provider_task_id="provider-task-1",
            occurred_at=now + timedelta(seconds=2),
        ),
    )
    provider = AcceptingProviderResolutions()
    reconciler = GenerationAttemptReconciler(
        attempts,
        provider,
        generation_tasks=tasks,
        clock=lambda: now + timedelta(minutes=1),
    )

    replay = reconciler.reconcile("account-space-1", "task-1")
    second_replay = reconciler.reconcile("account-space-1", "task-1")

    assert replay == existing
    assert second_replay == existing
    running_task = tasks.get("account-space-1", "task-1")
    assert running_task.status is GenerationTaskStatus.RUNNING
    assert running_task.provider_task_id == existing.provider_task_id
    assert provider.requests == []


def test_reconciling_an_unknown_attempt_records_a_sanitized_confirmed_rejection() -> None:
    attempts, tasks, credits, prepared, now = _prepared_attempt()
    attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptSubmissionStarted(occurred_at=now + timedelta(seconds=1)),
    )
    unknown = attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptSubmissionUnknown(
            reason="provider submission status is unknown",
            occurred_at=now + timedelta(seconds=2),
        ),
    )
    reconciler = GenerationAttemptReconciler(
        attempts,
        RejectingProviderResolutions(),
        generation_tasks=tasks,
        clock=lambda: now + timedelta(seconds=3),
    )

    reconciled = reconciler.reconcile("account-space-1", "task-1")

    assert reconciled.status is GenerationAttemptStatus.FAILED
    assert reconciled.attempt_id == unknown.attempt_id
    assert reconciled.route_id == unknown.route_id
    assert reconciled.provider_idempotency_key == unknown.provider_idempotency_key
    assert reconciled.provider_cost_rate_id == unknown.provider_cost_rate_id
    assert reconciled.error_code == "not_accepted"
    assert reconciled.error == "provider lookup used credential=<redacted>"
    assert "provider-secret" not in repr(reconciled)
    assert tasks.get("account-space-1", "task-1").status is GenerationTaskStatus.FAILED
    assert credits.statement("account-space-1").frozen_credits == "0.0000"


def test_reconciling_the_second_attempt_as_rejected_exhausts_the_task() -> None:
    attempts, tasks, credits, prepared, now = _prepared_attempt()
    attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptSubmissionStarted(occurred_at=now + timedelta(seconds=1)),
    )
    first = attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptRejected(
            error_code="not_accepted",
            reason="provider confirmed the request was not accepted",
            occurred_at=now + timedelta(seconds=2),
        ),
    )
    second = attempts.prepare(
        GenerationAttemptPreparation(
            account_space_id="account-space-1",
            task_id="task-1",
            route_id="route-1",
            occurred_at=now + timedelta(seconds=3),
        )
    )
    attempts.transition(
        "account-space-1",
        second.attempt_id,
        AttemptSubmissionStarted(occurred_at=now + timedelta(seconds=4)),
    )
    unknown = attempts.transition(
        "account-space-1",
        second.attempt_id,
        AttemptSubmissionUnknown(
            reason="provider submission status is unknown",
            occurred_at=now + timedelta(seconds=5),
        ),
    )
    reconciler = GenerationAttemptReconciler(
        attempts,
        RejectingProviderResolutions(),
        generation_tasks=tasks,
        clock=lambda: now + timedelta(seconds=6),
    )

    reconciled = reconciler.reconcile("account-space-1", "task-1")

    assert reconciled.status is GenerationAttemptStatus.FAILED
    assert reconciled.attempt_id == unknown.attempt_id
    assert reconciled.attempt_no == 2
    assert attempts.for_task("account-space-1", "task-1") == (first, reconciled)
    failed_task = tasks.get("account-space-1", "task-1")
    assert failed_task.status is GenerationTaskStatus.FAILED
    assert failed_task.error == "provider lookup used credential=<redacted>"
    assert failed_task.outcome_reference == "generation-attempt:attempt-2"
    assert credits.statement("account-space-1").frozen_credits == "0.0000"


def test_reconciling_an_already_failed_second_attempt_finishes_the_task_without_provider_lookup() -> None:
    attempts, tasks, credits, prepared, now = _prepared_attempt()
    attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptSubmissionStarted(occurred_at=now + timedelta(seconds=1)),
    )
    attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptRejected(
            error_code="not_accepted",
            reason="provider confirmed the request was not accepted",
            occurred_at=now + timedelta(seconds=2),
        ),
    )
    second = attempts.prepare(
        GenerationAttemptPreparation(
            account_space_id="account-space-1",
            task_id="task-1",
            route_id="route-1",
            occurred_at=now + timedelta(seconds=3),
        )
    )
    attempts.transition(
        "account-space-1",
        second.attempt_id,
        AttemptSubmissionStarted(occurred_at=now + timedelta(seconds=4)),
    )
    failed = attempts.transition(
        "account-space-1",
        second.attempt_id,
        AttemptRejected(
            error_code="not_accepted",
            reason="provider confirmed the request was not accepted",
            occurred_at=now + timedelta(seconds=5),
        ),
    )
    provider = AcceptingProviderResolutions()
    reconciler = GenerationAttemptReconciler(
        attempts,
        provider,
        generation_tasks=tasks,
        clock=lambda: now + timedelta(seconds=6),
    )

    replay = reconciler.reconcile("account-space-1", "task-1")

    assert replay == failed
    assert provider.requests == []
    assert tasks.get("account-space-1", "task-1").status is GenerationTaskStatus.FAILED
    assert credits.statement("account-space-1").frozen_credits == "0.0000"


@pytest.mark.parametrize("provider", [UnknownProviderResolutions(), FailingProviderResolutions()])
def test_reconciling_without_a_confirmed_result_preserves_the_unknown_attempt(provider: object) -> None:
    attempts, tasks, credits, prepared, now = _prepared_attempt()
    attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptSubmissionStarted(occurred_at=now + timedelta(seconds=1)),
    )
    unknown = attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptSubmissionUnknown(
            reason="provider submission status is unknown",
            occurred_at=now + timedelta(seconds=2),
        ),
    )
    reconciler = GenerationAttemptReconciler(  # type: ignore[arg-type]
        attempts,
        provider,
        generation_tasks=tasks,
        clock=lambda: now + timedelta(seconds=3),
    )

    reconciled = reconciler.reconcile("account-space-1", "task-1")

    assert reconciled == unknown
    assert attempts.for_task("account-space-1", "task-1") == (unknown,)
    assert "provider-secret" not in repr(reconciled)
    assert tasks.get("account-space-1", "task-1").status is GenerationTaskStatus.QUEUED
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def test_submitting_a_generation_attempt_records_a_sanitized_explicit_rejection() -> None:
    attempts, tasks, credits, prepared, now = _prepared_attempt()
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        RejectingProviderSubmissions(),
        clock=lambda: now + timedelta(seconds=1),
    )

    submitted = submitter.submit("account-space-1", "task-1")

    assert submitted.status is GenerationAttemptStatus.FAILED
    assert submitted.attempt_id == prepared.attempt_id
    assert submitted.route_id == prepared.route_id
    assert submitted.provider_idempotency_key == prepared.provider_idempotency_key
    assert submitted.error_code == "quota_exceeded"
    assert submitted.error == "request rejected with credential=<redacted>"
    assert "provider-secret" not in repr(submitted)
    assert tasks.get("account-space-1", "task-1").status is GenerationTaskStatus.FAILED
    assert credits.statement("account-space-1").frozen_credits == "0.0000"


def test_submitting_after_explicit_rejection_does_not_create_or_send_another_attempt() -> None:
    tasks, credits, now = _funded_tasks()
    task = tasks.submit(
        GenerationSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="测试生成请求",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
            selected_route_id="route-1",
            route_selection_reason="automatic",
        )
    )
    costs = InMemoryProviderCostRates(
        id_factory=iter(("cost-rate-1", "cost-rate-2")).__next__,
        clock=lambda: now,
    )
    first_cost = costs.publish(
        "route-1",
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=120_000,
        effective_from=now,
    )
    attempts = InMemoryGenerationAttempts(
        tasks,
        provider_cost_rates=costs,
        id_factory=iter(("attempt-1", "attempt-2")).__next__,
    )
    first = GenerationAttemptSubmitter(
        tasks,
        attempts,
        RejectingProviderSubmissions(),
        clock=lambda: now + timedelta(seconds=1),
    ).submit("account-space-1", task.task_id)
    costs.publish(
        "route-1",
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=130_000,
        effective_from=now + timedelta(seconds=2),
    )
    provider = AcceptingProviderSubmissions()

    retried = GenerationAttemptSubmitter(
        tasks,
        attempts,
        provider,
        clock=lambda: now + timedelta(seconds=3),
    ).submit("account-space-1", task.task_id)

    assert first.status is GenerationAttemptStatus.FAILED
    assert first.provider_cost_rate_id == first_cost.version_id
    assert retried == first
    assert attempts.for_task("account-space-1", task.task_id) == (first,)
    assert provider.requests == []
    persisted_task = tasks.get("account-space-1", task.task_id)
    assert persisted_task.model_price_version_id == task.model_price_version_id
    assert persisted_task.status is GenerationTaskStatus.FAILED
    assert credits.statement("account-space-1").frozen_credits == "0.0000"


def test_explicit_rejection_immediately_fails_and_replays_without_another_attempt() -> None:
    attempts, tasks, credits, now = _attempt_dependencies()
    first = GenerationAttemptSubmitter(
        tasks,
        attempts,
        RejectingProviderSubmissions(),
        clock=lambda: now + timedelta(seconds=1),
    ).submit("account-space-1", "task-1")
    second = GenerationAttemptSubmitter(
        tasks,
        attempts,
        RejectingProviderSubmissions(),
        clock=lambda: now + timedelta(seconds=2),
    ).submit("account-space-1", "task-1")

    failed_task = tasks.get("account-space-1", "task-1")
    provider = AcceptingProviderSubmissions()
    replay = GenerationAttemptSubmitter(
        tasks,
        attempts,
        provider,
        clock=lambda: now + timedelta(seconds=3),
    ).submit("account-space-1", "task-1")

    assert first.status is GenerationAttemptStatus.FAILED
    assert second.status is GenerationAttemptStatus.FAILED
    assert second == first
    assert attempts.for_task("account-space-1", "task-1") == (first,)
    assert failed_task.status is GenerationTaskStatus.FAILED
    assert failed_task.error == "request rejected with credential=<redacted>"
    assert failed_task.outcome_reference == "generation-attempt:attempt-1"
    assert credits.statement("account-space-1").frozen_credits == "0.0000"
    assert replay == second
    assert provider.requests == []


def test_submitting_a_generation_attempt_records_a_sanitized_unknown_result() -> None:
    attempts, tasks, credits, prepared, now = _prepared_attempt()
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        UnknownProviderSubmissions(),
        clock=lambda: now + timedelta(seconds=1),
    )

    submitted = submitter.submit("account-space-1", "task-1")

    assert submitted.status is GenerationAttemptStatus.UNKNOWN
    assert submitted.attempt_id == prepared.attempt_id
    assert submitted.route_id == prepared.route_id
    assert submitted.provider_idempotency_key == prepared.provider_idempotency_key
    assert submitted.error == "provider submission status is unknown"
    assert "provider-secret" not in repr(submitted)
    assert tasks.get("account-space-1", "task-1").status is GenerationTaskStatus.RUNNING
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def test_provider_submission_exception_leaves_the_same_attempt_safely_unknown() -> None:
    attempts, tasks, credits, prepared, now = _prepared_attempt()
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        FailingProviderSubmissions(),
        clock=lambda: now + timedelta(seconds=1),
    )

    submitted = submitter.submit("account-space-1", "task-1")

    assert submitted.status is GenerationAttemptStatus.UNKNOWN
    assert submitted.attempt_id == prepared.attempt_id
    assert submitted.route_id == prepared.route_id
    assert submitted.provider_idempotency_key == prepared.provider_idempotency_key
    assert submitted.error == "provider submission status is unknown"
    assert "provider-secret" not in repr(submitted)
    assert tasks.get("account-space-1", "task-1").status is GenerationTaskStatus.RUNNING
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def test_invalid_provider_response_leaves_the_same_attempt_safely_unknown() -> None:
    attempts, tasks, credits, prepared, now = _prepared_attempt()
    submitter = GenerationAttemptSubmitter(  # type: ignore[arg-type]
        tasks,
        attempts,
        InvalidProviderSubmissions(),
        clock=lambda: now + timedelta(seconds=1),
    )

    submitted = submitter.submit("account-space-1", "task-1")

    assert submitted.status is GenerationAttemptStatus.UNKNOWN
    assert submitted.attempt_id == prepared.attempt_id
    assert submitted.route_id == prepared.route_id
    assert submitted.provider_idempotency_key == prepared.provider_idempotency_key
    assert submitted.error == "provider submission status is unknown"
    assert "provider-secret" not in repr(submitted)
    assert tasks.get("account-space-1", "task-1").status is GenerationTaskStatus.RUNNING
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def test_accepted_response_without_provider_task_id_is_safely_unknown() -> None:
    attempts, tasks, credits, prepared, now = _prepared_attempt()
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        MissingProviderTaskIdSubmissions(),
        clock=lambda: now + timedelta(seconds=1),
    )

    submitted = submitter.submit("account-space-1", "task-1")

    assert submitted.status is GenerationAttemptStatus.UNKNOWN
    assert submitted.attempt_id == prepared.attempt_id
    assert submitted.route_id == prepared.route_id
    assert submitted.provider_idempotency_key == prepared.provider_idempotency_key
    assert submitted.error == "provider submission status is unknown"
    assert tasks.get("account-space-1", "task-1").status is GenerationTaskStatus.RUNNING
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def test_legacy_submitting_attempt_recovers_to_unknown_without_replaying_provider_request() -> None:
    attempts, tasks, credits, prepared, now = _prepared_attempt()
    submitting = attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptSubmissionStarted(occurred_at=now + timedelta(seconds=1)),
    )
    provider = AcceptingProviderSubmissions()
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        provider,
        clock=lambda: now + timedelta(minutes=1),
    )

    recovered = submitter.submit("account-space-1", "task-1")

    assert recovered.status is GenerationAttemptStatus.UNKNOWN
    assert recovered.attempt_id == submitting.attempt_id
    assert recovered.route_id == submitting.route_id
    assert recovered.provider_idempotency_key == submitting.provider_idempotency_key
    assert recovered.error == "provider submission was interrupted before its outcome was recorded"
    assert provider.requests == []
    assert tasks.get("account-space-1", "task-1").status is GenerationTaskStatus.RUNNING
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def test_provider_pending_attempt_starts_queued_task_without_resubmission() -> None:
    attempts, tasks, _, prepared, now = _prepared_attempt()
    attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptSubmissionStarted(occurred_at=now + timedelta(seconds=1)),
    )
    existing = attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptAccepted(
            provider_task_id="provider-task-1",
            occurred_at=now + timedelta(seconds=2),
        ),
    )
    provider = AcceptingProviderSubmissions()
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        provider,
        clock=lambda: now + timedelta(minutes=1),
    )

    replay = submitter.submit("account-space-1", "task-1")
    second_replay = submitter.submit("account-space-1", "task-1")

    assert replay == existing
    assert second_replay == existing
    running_task = tasks.get("account-space-1", "task-1")
    assert running_task.status is GenerationTaskStatus.RUNNING
    assert running_task.provider_task_id == existing.provider_task_id
    assert provider.requests == []


def test_unknown_attempt_is_returned_without_resubmission() -> None:
    attempts, tasks, _, prepared, now = _prepared_attempt()
    attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptSubmissionStarted(occurred_at=now + timedelta(seconds=1)),
    )
    existing = attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptSubmissionUnknown(
            reason="provider submission status is unknown",
            occurred_at=now + timedelta(seconds=2),
        ),
    )
    provider = AcceptingProviderSubmissions()
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        provider,
        clock=lambda: now + timedelta(minutes=1),
    )

    replay = submitter.submit("account-space-1", "task-1")

    assert replay == existing
    assert tasks.get("account-space-1", "task-1").status is GenerationTaskStatus.RUNNING
    assert provider.requests == []


def test_submitting_another_account_space_task_is_treated_as_not_found() -> None:
    attempts, tasks, _, _, now = _prepared_attempt()
    submitter = GenerationAttemptSubmitter(
        tasks,
        attempts,
        AcceptingProviderSubmissions(),
        clock=lambda: now + timedelta(seconds=1),
    )

    with pytest.raises(GenerationAttemptNotFound):
        submitter.submit("other-account", "task-1")


def test_preparing_the_first_generation_attempt_is_stable_and_does_not_start_the_task() -> None:
    tasks, credits, now = _funded_tasks()
    task = tasks.submit(
        GenerationSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="测试生成请求",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
            selected_route_id="route-1",
            route_selection_reason="automatic",
        )
    )
    attempts = InMemoryGenerationAttempts(
        tasks,
        provider_cost_rates=_provider_costs(now),
        id_factory=lambda: "attempt-1",
    )
    preparation = GenerationAttemptPreparation(
        account_space_id="account-space-1",
        task_id=task.task_id,
        route_id="route-1",
        occurred_at=now,
    )

    prepared = attempts.prepare(preparation)
    replay = attempts.prepare(
        GenerationAttemptPreparation(
            account_space_id="account-space-1",
            task_id=task.task_id,
            route_id="route-1",
            occurred_at=now + timedelta(minutes=1),
        )
    )

    assert replay == prepared
    assert prepared.attempt_id == "attempt-1"
    assert prepared.attempt_no == 1
    assert prepared.route_id == "route-1"
    assert prepared.provider_idempotency_key
    assert prepared.status is GenerationAttemptStatus.CREATED
    assert attempts.for_task("account-space-1", task.task_id) == (prepared,)
    assert tasks.get("account-space-1", task.task_id).status is GenerationTaskStatus.QUEUED
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def test_missing_provider_cost_prevents_attempt_creation_and_provider_submission() -> None:
    tasks, credits, now = _funded_tasks()
    task = tasks.submit(
        GenerationSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="测试生成请求",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
            selected_route_id="route-1",
            route_selection_reason="automatic",
        )
    )
    attempts = InMemoryGenerationAttempts(
        tasks,
        provider_cost_rates=InMemoryProviderCostRates(clock=lambda: now),
    )
    provider = AcceptingProviderSubmissions()
    submitter = GenerationAttemptSubmitter(tasks, attempts, provider, clock=lambda: now)

    with pytest.raises(ProviderCostRateNotFound):
        submitter.submit("account-space-1", task.task_id)

    assert attempts.for_task("account-space-1", task.task_id) == ()
    assert provider.requests == []
    assert tasks.get("account-space-1", task.task_id).status is GenerationTaskStatus.QUEUED
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def test_preparing_the_first_attempt_freezes_the_effective_provider_cost_version() -> None:
    tasks, credits, now = _funded_tasks()
    task = tasks.submit(
        GenerationSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="测试生成请求",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
            selected_route_id="route-1",
            route_selection_reason="automatic",
        )
    )
    costs = InMemoryProviderCostRates(
        id_factory=iter(("cost-rate-1", "cost-rate-2")).__next__,
        clock=lambda: now,
    )
    first_cost = costs.publish(
        "route-1",
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=120_000,
        effective_from=now,
    )
    attempts = InMemoryGenerationAttempts(
        tasks,
        provider_cost_rates=costs,
        id_factory=lambda: "attempt-1",
    )

    prepared = attempts.prepare(
        GenerationAttemptPreparation(
            account_space_id="account-space-1",
            task_id=task.task_id,
            route_id="route-1",
            occurred_at=now,
        )
    )
    costs.publish(
        "route-1",
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=130_000,
        effective_from=now + timedelta(minutes=1),
    )
    replay = attempts.prepare(
        GenerationAttemptPreparation(
            account_space_id="account-space-1",
            task_id=task.task_id,
            route_id="route-1",
            occurred_at=now + timedelta(minutes=2),
        )
    )

    assert replay == prepared
    assert prepared.provider_cost_rate_id == first_cost.version_id
    assert tasks.get("account-space-1", task.task_id).status is GenerationTaskStatus.QUEUED
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def test_preparing_the_first_attempt_rejects_a_route_other_than_the_task_selection() -> None:
    tasks, _, now = _funded_tasks()
    tasks.submit(
        GenerationSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="测试生成请求",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
            selected_route_id="route-1",
            route_selection_reason="automatic",
        )
    )
    attempts = InMemoryGenerationAttempts(
        tasks,
        provider_cost_rates=InMemoryProviderCostRates(clock=lambda: now),
    )

    with pytest.raises(GenerationAttemptConflict):
        attempts.prepare(
            GenerationAttemptPreparation(
                account_space_id="account-space-1",
                task_id="task-1",
                route_id="route-2",
                occurred_at=now,
            )
        )


def test_attempt_submission_records_acceptance_idempotently_without_changing_the_task() -> None:
    attempts, tasks, credits, prepared, now = _prepared_attempt()
    started_event = AttemptSubmissionStarted(occurred_at=now + timedelta(seconds=1))

    started = attempts.transition("account-space-1", prepared.attempt_id, started_event)

    assert started.status is GenerationAttemptStatus.SUBMITTING
    assert started.submitted_at == started_event.occurred_at
    assert attempts.transition("account-space-1", prepared.attempt_id, started_event) == started

    accepted_event = AttemptAccepted(
        provider_task_id="provider-task-1",
        occurred_at=now + timedelta(seconds=2),
    )
    accepted = attempts.transition("account-space-1", prepared.attempt_id, accepted_event)

    assert accepted.status is GenerationAttemptStatus.PROVIDER_PENDING
    assert accepted.provider_task_id == "provider-task-1"
    assert accepted.accepted_at == accepted_event.occurred_at
    assert accepted.provider_idempotency_key == prepared.provider_idempotency_key
    assert attempts.transition("account-space-1", prepared.attempt_id, accepted_event) == accepted
    with pytest.raises(GenerationAttemptConflict):
        attempts.transition(
            "account-space-1",
            prepared.attempt_id,
            AttemptAccepted(provider_task_id="different-provider-task", occurred_at=accepted_event.occurred_at),
        )
    assert tasks.get("account-space-1", "task-1").status is GenerationTaskStatus.QUEUED
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def test_unknown_submission_stays_on_the_same_attempt_and_hides_cross_account_access() -> None:
    attempts, _, _, prepared, now = _prepared_attempt()
    attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptSubmissionStarted(occurred_at=now + timedelta(seconds=1)),
    )
    unknown_event = AttemptSubmissionUnknown(
        reason="submission timed out after request bytes were sent",
        occurred_at=now + timedelta(seconds=2),
    )

    unknown = attempts.transition("account-space-1", prepared.attempt_id, unknown_event)

    assert unknown.status is GenerationAttemptStatus.UNKNOWN
    assert unknown.error == unknown_event.reason
    assert unknown.finished_at is None
    assert attempts.transition("account-space-1", prepared.attempt_id, unknown_event) == unknown
    assert (
        attempts.prepare(
            GenerationAttemptPreparation(
                account_space_id="account-space-1",
                task_id="task-1",
                route_id="route-1",
                occurred_at=now + timedelta(minutes=1),
            )
        )
        == unknown
    )
    with pytest.raises(GenerationAttemptNotFound):
        attempts.transition("other-account", prepared.attempt_id, unknown_event)


def test_unknown_submission_can_be_confirmed_accepted_on_the_same_attempt() -> None:
    attempts, tasks, credits, prepared, now = _prepared_attempt()
    attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptSubmissionStarted(occurred_at=now + timedelta(seconds=1)),
    )
    unknown = attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptSubmissionUnknown(
            reason="submission timed out after request bytes were sent",
            occurred_at=now + timedelta(seconds=2),
        ),
    )
    accepted_event = AttemptAccepted(
        provider_task_id="provider-task-1",
        occurred_at=now + timedelta(seconds=3),
    )

    accepted = attempts.transition("account-space-1", prepared.attempt_id, accepted_event)

    assert accepted.status is GenerationAttemptStatus.PROVIDER_PENDING
    assert accepted.attempt_id == unknown.attempt_id
    assert accepted.route_id == unknown.route_id
    assert accepted.provider_idempotency_key == unknown.provider_idempotency_key
    assert accepted.provider_task_id == "provider-task-1"
    assert attempts.transition("account-space-1", prepared.attempt_id, accepted_event) == accepted
    assert tasks.get("account-space-1", "task-1").status is GenerationTaskStatus.QUEUED
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def test_explicit_rejection_finishes_only_the_attempt() -> None:
    attempts, tasks, credits, prepared, now = _prepared_attempt()
    attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptSubmissionStarted(occurred_at=now + timedelta(seconds=1)),
    )
    rejected_event = AttemptRejected(
        error_code="upstream_rejected",
        reason="provider explicitly rejected the request",
        occurred_at=now + timedelta(seconds=2),
    )

    rejected = attempts.transition("account-space-1", prepared.attempt_id, rejected_event)

    assert rejected.status is GenerationAttemptStatus.FAILED
    assert rejected.error_code == "upstream_rejected"
    assert rejected.error == rejected_event.reason
    assert rejected.finished_at == rejected_event.occurred_at
    assert attempts.transition("account-space-1", prepared.attempt_id, rejected_event) == rejected
    assert tasks.get("account-space-1", "task-1").status is GenerationTaskStatus.QUEUED
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def test_unknown_submission_can_be_confirmed_rejected_on_the_same_attempt() -> None:
    attempts, tasks, credits, prepared, now = _prepared_attempt()
    attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptSubmissionStarted(occurred_at=now + timedelta(seconds=1)),
    )
    unknown = attempts.transition(
        "account-space-1",
        prepared.attempt_id,
        AttemptSubmissionUnknown(
            reason="submission timed out after request bytes were sent",
            occurred_at=now + timedelta(seconds=2),
        ),
    )
    rejected_event = AttemptRejected(
        error_code="not_accepted",
        reason="provider confirmed the request was not accepted",
        occurred_at=now + timedelta(seconds=3),
    )

    rejected = attempts.transition("account-space-1", prepared.attempt_id, rejected_event)

    assert rejected.status is GenerationAttemptStatus.FAILED
    assert rejected.attempt_id == unknown.attempt_id
    assert rejected.route_id == unknown.route_id
    assert rejected.provider_idempotency_key == unknown.provider_idempotency_key
    assert rejected.error_code == "not_accepted"
    assert rejected.error == "provider confirmed the request was not accepted"
    assert rejected.finished_at == rejected_event.occurred_at
    assert attempts.transition("account-space-1", prepared.attempt_id, rejected_event) == rejected
    assert tasks.get("account-space-1", "task-1").status is GenerationTaskStatus.QUEUED
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def _funded_tasks() -> tuple[InMemoryGenerationTasks, InMemoryCredits, datetime]:
    now = datetime(2026, 8, 8, 14, 0, tzinfo=UTC)
    prices = InMemoryModelPrices(clock=lambda: now)
    credits = InMemoryCredits(
        clock=lambda: now,
        account_space_ids={"account-space-1"},
        model_prices=prices,
    )
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    canvases = InMemoryCanvases(id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id="user-1",
            account_space_id="account-space-1",
            title="生成画布",
            kind="classic",
            created_at=now,
        )
    )
    return InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2), credits, now


def _attempt_dependencies():
    tasks, credits, now = _funded_tasks()
    tasks.submit(
        GenerationSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="测试生成请求",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
            selected_route_id="route-1",
            route_selection_reason="automatic",
        )
    )
    attempts = InMemoryGenerationAttempts(
        tasks,
        provider_cost_rates=_provider_costs(now),
        id_factory=iter(("attempt-1", "attempt-2", "attempt-3")).__next__,
    )
    return attempts, tasks, credits, now


def _provider_costs(now: datetime) -> InMemoryProviderCostRates:
    costs = InMemoryProviderCostRates(id_factory=lambda: "cost-rate-1", clock=lambda: now)
    costs.publish(
        "route-1",
        variant_code="4k",
        provider_currency="USD",
        cost_per_image_micros=120_000,
        effective_from=now,
    )
    return costs


def _prepared_attempt():
    attempts, tasks, credits, now = _attempt_dependencies()
    prepared = attempts.prepare(
        GenerationAttemptPreparation(
            account_space_id="account-space-1",
            task_id="task-1",
            route_id="route-1",
            occurred_at=now,
        )
    )
    return attempts, tasks, credits, prepared, now
