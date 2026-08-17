from datetime import UTC, datetime, timedelta

from app.canvases import CanvasCreation, InMemoryCanvases
from app.credits import InMemoryCredits
from app.generation import GenerationParameters, GenerationStarted, GenerationSubmission, InMemoryGenerationTasks
from app.media import (
    GeneratedMediaRegistration,
    GeneratedMediaState,
    InMemoryGeneratedMedia,
    InMemoryStorageAllowances,
    MediaObjectDeletionFailed,
)


class _RecordingMediaObjects:
    def __init__(self, object_keys: set[str]) -> None:
        self.object_keys = object_keys
        self.deleted_keys: list[str] = []

    def delete(self, object_key: str) -> None:
        self.deleted_keys.append(object_key)
        self.object_keys.discard(object_key)


class _FailOnceMediaObjects:
    def __init__(self) -> None:
        self.attempts = 0

    def delete(self, object_key: str) -> None:
        self.attempts += 1
        if self.attempts == 1:
            raise MediaObjectDeletionFailed(object_key)


def test_expiration_deletes_only_due_objects_and_marks_their_metadata_expired() -> None:
    now = datetime(2026, 8, 9, 16, 0, tzinfo=UTC)
    task_started_at = now - timedelta(hours=25)
    credits = InMemoryCredits(clock=lambda: task_started_at, account_space_ids={"account-space-1"})
    package = credits.publish(
        "starter",
        payment_cny="1.00",
        credits="1.0000",
        effective_from=task_started_at,
    )
    credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-1",
        occurred_at=task_started_at,
    )
    canvases = InMemoryCanvases(id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id="user-1",
            account_space_id="account-space-1",
            title="媒体清理画布",
            kind="classic",
            created_at=task_started_at,
        )
    )
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    tasks.submit(
        GenerationSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=2,
            prompt="测试生成请求",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=task_started_at,
        )
    )
    tasks.transition(
        "account-space-1",
        "task-1",
        GenerationStarted(provider_task_id="provider-task-1", occurred_at=task_started_at),
    )
    objects = _RecordingMediaObjects({"temporary/old.png", "temporary/fresh.png"})
    generated_media = InMemoryGeneratedMedia(
        tasks,
        media_objects=objects,
        storage_allowances=InMemoryStorageAllowances({}),
        id_factory=iter(("old-media", "fresh-media")).__next__,
    )
    old_media = generated_media.register(
        GeneratedMediaRegistration(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            result_reference="old-result",
            object_key="temporary/old.png",
            kind="image",
            mime_type="image/png",
            size_bytes=100,
            content_hash="a" * 64,
            created_at=task_started_at,
        )
    )
    fresh_media = generated_media.register(
        GeneratedMediaRegistration(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            result_reference="fresh-result",
            object_key="temporary/fresh.png",
            kind="image",
            mime_type="image/png",
            size_bytes=100,
            content_hash="b" * 64,
            created_at=now - timedelta(hours=11),
        )
    )

    assert old_media.expires_at == task_started_at + timedelta(hours=24)
    assert fresh_media.expires_at == now + timedelta(hours=13)

    report = generated_media.expire_due(now)
    replay = generated_media.expire_due(now)

    assert report.expired_media_ids == (old_media.media_id,)
    assert report.failed_media_ids == ()
    assert replay.expired_media_ids == ()
    assert objects.deleted_keys == ["temporary/old.png"]
    assert generated_media.get("account-space-1", old_media.media_id).state is GeneratedMediaState.EXPIRED
    assert generated_media.get("account-space-1", fresh_media.media_id).state is GeneratedMediaState.TEMPORARY


def test_object_deletion_failure_keeps_temporary_media_for_retry() -> None:
    now = datetime(2026, 8, 9, 16, 0, tzinfo=UTC)
    task_started_at = now - timedelta(hours=25)
    credits = InMemoryCredits(clock=lambda: task_started_at, account_space_ids={"account-space-1"})
    package = credits.publish(
        "starter",
        payment_cny="1.00",
        credits="1.0000",
        effective_from=task_started_at,
    )
    credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-1",
        occurred_at=task_started_at,
    )
    canvases = InMemoryCanvases(id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id="user-1",
            account_space_id="account-space-1",
            title="媒体重试画布",
            kind="classic",
            created_at=task_started_at,
        )
    )
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
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
            submitted_at=task_started_at,
        )
    )
    tasks.transition(
        "account-space-1",
        "task-1",
        GenerationStarted(provider_task_id="provider-task-1", occurred_at=task_started_at),
    )
    objects = _FailOnceMediaObjects()
    generated_media = InMemoryGeneratedMedia(
        tasks,
        media_objects=objects,
        storage_allowances=InMemoryStorageAllowances({}),
        id_factory=lambda: "media-1",
    )
    media = generated_media.register(
        GeneratedMediaRegistration(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            result_reference="result-1",
            object_key="temporary/retry.png",
            kind="image",
            mime_type="image/png",
            size_bytes=100,
            content_hash="a" * 64,
            created_at=task_started_at,
        )
    )

    failed = generated_media.expire_due(now)
    retried = generated_media.expire_due(now)

    assert failed.failed_media_ids == (media.media_id,)
    assert failed.expired_media_ids == ()
    assert retried.expired_media_ids == (media.media_id,)
    assert objects.attempts == 2
    assert generated_media.get("account-space-1", media.media_id).state is GeneratedMediaState.EXPIRED
