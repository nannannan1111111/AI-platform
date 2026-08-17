from datetime import UTC, datetime

from app.canvases import CanvasCreation, InMemoryCanvases
from app.credits import InMemoryCredits
from app.generation import GenerationParameters, GenerationStarted, GenerationSubmission, InMemoryGenerationTasks
from app.media import (
    GeneratedMediaRegistration,
    GeneratedMediaState,
    InMemoryGeneratedMedia,
    InMemoryMediaObjects,
    InMemoryStorageAllowances,
    MediaObjectDeletionFailed,
)


class _FailOnDeleteMediaObjects:
    def delete(self, object_key: str) -> None:
        raise MediaObjectDeletionFailed(object_key)

    def promote(self, temporary_key: str, persistent_key: str) -> None:
        pass


def test_same_account_content_hash_counts_once_against_storage_allowance() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={"account-space-1"})
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
            title="去重画布",
            kind="classic",
            created_at=now,
        )
    )
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=3)
    tasks.submit(
        GenerationSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=3,
            prompt="测试生成请求",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
        )
    )
    tasks.transition(
        "account-space-1",
        "task-1",
        GenerationStarted(provider_task_id="provider-task-1", occurred_at=now),
    )
    temporary_keys = {"temporary/duplicate-1.png", "temporary/duplicate-2.png", "temporary/new.png"}
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=InMemoryMediaObjects(temporary_keys),
        storage_allowances=InMemoryStorageAllowances({"account-space-1": 101}),
        id_factory=iter(("media-1", "media-2", "media-3")).__next__,
    )
    records = tuple(
        media.register(
            GeneratedMediaRegistration(
                user_id="user-1",
                account_space_id="account-space-1",
                canvas_id="canvas-1",
                task_id="task-1",
                result_reference=f"result-{index}",
                object_key=object_key,
                kind="image",
                mime_type="image/png",
                size_bytes=size_bytes,
                content_hash=content_hash,
                created_at=now,
            )
        )
        for index, object_key, size_bytes, content_hash in (
            (1, "temporary/duplicate-1.png", 100, "a" * 64),
            (2, "temporary/duplicate-2.png", 100, "a" * 64),
            (3, "temporary/new.png", 1, "b" * 64),
        )
    )

    retained = tuple(media.retain_to_canvas("account-space-1", record.media_id, now) for record in records)

    assert tuple(record.state for record in retained) == (
        GeneratedMediaState.PERSISTENT,
        GeneratedMediaState.PERSISTENT,
        GeneratedMediaState.PERSISTENT,
    )
    assert retained[0].object_key == retained[1].object_key
    assert retained[2].object_key != retained[0].object_key

    allowance = media.storage_allowance("account-space-1")

    assert allowance.limit_bytes == 101
    assert allowance.used_bytes == 101
    assert allowance.available_bytes == 0


def test_temporary_results_already_count_against_storage_allowance() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={"account-space-1"})
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-temporary",
        occurred_at=now,
    )
    tasks = InMemoryGenerationTasks(credits, canvases=InMemoryCanvases(), max_active_tasks=2)
    tasks.submit(
        GenerationSubmission(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id=None,
            task_id="task-temporary",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="temporary storage usage",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
        )
    )
    tasks.transition(
        "account-space-1",
        "task-temporary",
        GenerationStarted(provider_task_id="provider-temporary", occurred_at=now),
    )
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=InMemoryMediaObjects({"temporary/result.png"}),
        storage_allowances=InMemoryStorageAllowances({"account-space-1": 20 * 1024 * 1024}),
        id_factory=lambda: "media-temporary",
    )
    media.register(
        GeneratedMediaRegistration(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id=None,
            task_id="task-temporary",
            result_reference="result-temporary",
            object_key="temporary/result.png",
            kind="image",
            mime_type="image/png",
            size_bytes=3 * 1024 * 1024,
            content_hash="e" * 64,
            created_at=now,
        )
    )

    allowance = media.storage_allowance("account-space-1")

    assert allowance.used_bytes == 3 * 1024 * 1024
    assert allowance.available_bytes == 17 * 1024 * 1024


def test_removing_the_only_canvas_reference_releases_persistent_media() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={"account-space-1"})
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
            title="待释放媒体的画布",
            kind="classic",
            created_at=now,
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
            submitted_at=now,
        )
    )
    tasks.transition(
        "account-space-1",
        "task-1",
        GenerationStarted(provider_task_id="provider-task-1", occurred_at=now),
    )
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=InMemoryMediaObjects({"temporary/result.png"}),
        storage_allowances=InMemoryStorageAllowances({"account-space-1": 100}),
        id_factory=lambda: "media-1",
    )
    registered = media.register(
        GeneratedMediaRegistration(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            result_reference="result-1",
            object_key="temporary/result.png",
            kind="image",
            mime_type="image/png",
            size_bytes=100,
            content_hash="a" * 64,
            created_at=now,
        )
    )
    retained = media.retain_to_canvas("account-space-1", registered.media_id, now)
    released_at = datetime(2026, 8, 10, 11, 0, tzinfo=UTC)

    result = media.reconcile_canvas_references("account-space-1", "canvas-1", (), released_at)

    assert result.released_media_ids == ("media-1",)
    released = media.get("account-space-1", retained.media_id)
    assert released.state is GeneratedMediaState.RELEASED
    assert released.released_at == released_at


def test_releasing_one_of_two_same_content_references_keeps_the_shared_object() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={"account-space-1"})
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        "account-space-1",
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    canvas_ids = iter(("canvas-1", "canvas-2"))
    canvases = InMemoryCanvases(id_factory=canvas_ids.__next__)
    for title in ("第一张画布", "第二张画布"):
        canvases.create(
            CanvasCreation(
                user_id="user-1",
                account_space_id="account-space-1",
                title=title,
                kind="classic",
                created_at=now,
            )
        )
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    for index in (1, 2):
        task_id = f"task-{index}"
        tasks.submit(
            GenerationSubmission(
                user_id="user-1",
                account_space_id="account-space-1",
                canvas_id=f"canvas-{index}",
                task_id=task_id,
                logical_model="gpt-image-2",
                output_spec="4k",
                quantity=1,
                prompt="测试生成请求",
                params=GenerationParameters(aspect_ratio="1:1"),
                submitted_at=now,
            )
        )
        tasks.transition(
            "account-space-1",
            task_id,
            GenerationStarted(provider_task_id=f"provider-task-{index}", occurred_at=now),
        )
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=_FailOnDeleteMediaObjects(),
        storage_allowances=InMemoryStorageAllowances({"account-space-1": 100}),
        id_factory=iter(("media-1", "media-2")).__next__,
    )
    records = tuple(
        media.register(
            GeneratedMediaRegistration(
                user_id="user-1",
                account_space_id="account-space-1",
                canvas_id=f"canvas-{index}",
                task_id=f"task-{index}",
                result_reference=f"result-{index}",
                object_key=f"temporary/result-{index}.png",
                kind="image",
                mime_type="image/png",
                size_bytes=100,
                content_hash="a" * 64,
                created_at=now,
            )
        )
        for index in (1, 2)
    )
    for record in records:
        media.retain_to_canvas("account-space-1", record.media_id, now)

    result = media.reconcile_canvas_references("account-space-1", "canvas-1", (), now)

    assert result.released_media_ids == ("media-1",)
    assert media.get("account-space-1", "media-1").state is GeneratedMediaState.RELEASED
    assert media.get("account-space-1", "media-2").state is GeneratedMediaState.PERSISTENT


def test_failed_last_object_deletion_preserves_the_canvas_reference_for_retry() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={"account-space-1"})
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
            title="待重试释放的画布",
            kind="classic",
            created_at=now,
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
            submitted_at=now,
        )
    )
    tasks.transition(
        "account-space-1",
        "task-1",
        GenerationStarted(provider_task_id="provider-task-1", occurred_at=now),
    )
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=_FailOnDeleteMediaObjects(),
        storage_allowances=InMemoryStorageAllowances({"account-space-1": 100}),
        id_factory=lambda: "media-1",
    )
    registered = media.register(
        GeneratedMediaRegistration(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            result_reference="result-1",
            object_key="temporary/result.png",
            kind="image",
            mime_type="image/png",
            size_bytes=100,
            content_hash="a" * 64,
            created_at=now,
        )
    )
    media.retain_to_canvas("account-space-1", registered.media_id, now)

    result = media.reconcile_canvas_references("account-space-1", "canvas-1", (), now)

    assert result.released_media_ids == ()
    assert result.failed_media_ids == ("media-1",)
    assert media.get("account-space-1", "media-1").state is GeneratedMediaState.PERSISTENT
