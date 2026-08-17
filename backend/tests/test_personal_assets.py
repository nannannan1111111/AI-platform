from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.assets import InMemoryPersonalAssets, PersonalAssetRename, PersonalAssetSave
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


class _FailOnceOnDeleteMediaObjects:
    def __init__(self, object_keys: set[str]) -> None:
        self._delegate = InMemoryMediaObjects(object_keys)
        self._delete_should_fail = True

    def delete(self, object_key: str) -> None:
        if self._delete_should_fail:
            self._delete_should_fail = False
            raise MediaObjectDeletionFailed(object_key)
        self._delegate.delete(object_key)

    def promote(self, temporary_key: str, persistent_key: str) -> None:
        self._delegate.promote(temporary_key, persistent_key)


def test_generated_media_is_saved_as_one_idempotent_personal_asset() -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
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
            title="资产来源画布",
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
    temporary_key = "temporary/account-space-1/task-1/result.png"
    generated_media = InMemoryGeneratedMedia(
        tasks,
        media_objects=InMemoryMediaObjects({temporary_key}),
        storage_allowances=InMemoryStorageAllowances({"account-space-1": 100}),
        id_factory=lambda: "media-1",
    )
    generated_media.register(
        GeneratedMediaRegistration(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            result_reference="result-1",
            object_key=temporary_key,
            kind="image",
            mime_type="image/png",
            size_bytes=100,
            content_hash="a" * 64,
            created_at=now,
        )
    )
    assets = InMemoryPersonalAssets(generated_media, id_factory=lambda: "asset-1")
    command = PersonalAssetSave(
        user_id="user-1",
        account_space_id="account-space-1",
        media_id="media-1",
        display_name="角色立绘",
        idempotency_key="save-result-1",
        saved_at=now,
    )

    saved = assets.save_generated_media(command)
    replay = assets.save_generated_media(replace(command, saved_at=now + timedelta(seconds=1)))

    assert replay == saved
    assert assets.list("account-space-1") == (saved,)
    assert saved.asset_id == "asset-1"
    assert saved.display_name == "角色立绘"
    assert saved.kind == "image"
    assert saved.mime_type == "image/png"
    assert saved.size_bytes == 100
    assert generated_media.get("account-space-1", "media-1").state is GeneratedMediaState.PERSISTENT


def test_personal_asset_reference_keeps_media_after_its_canvas_reference_is_removed() -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
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
            title="共享媒体画布",
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
    temporary_key = "temporary/account-space-1/task-1/shared.png"
    generated_media = InMemoryGeneratedMedia(
        tasks,
        media_objects=InMemoryMediaObjects({temporary_key}),
        storage_allowances=InMemoryStorageAllowances({"account-space-1": 100}),
        id_factory=lambda: "media-1",
    )
    generated_media.register(
        GeneratedMediaRegistration(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            result_reference="result-1",
            object_key=temporary_key,
            kind="image",
            mime_type="image/png",
            size_bytes=100,
            content_hash="a" * 64,
            created_at=now,
        )
    )
    generated_media.retain_to_canvas("account-space-1", "media-1", now)
    assets = InMemoryPersonalAssets(generated_media, id_factory=lambda: "asset-1")
    assets.save_generated_media(
        PersonalAssetSave(
            user_id="user-1",
            account_space_id="account-space-1",
            media_id="media-1",
            display_name="共享角色立绘",
            idempotency_key="save-shared-result",
            saved_at=now,
        )
    )

    result = generated_media.reconcile_canvas_references("account-space-1", "canvas-1", (), now)

    assert result.released_media_ids == ()
    assert generated_media.get("account-space-1", "media-1").state is GeneratedMediaState.PERSISTENT


def test_active_personal_asset_can_be_renamed_without_changing_its_media_reference() -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
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
            title="资产来源画布",
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
    temporary_key = "temporary/account-space-1/task-1/result.png"
    generated_media = InMemoryGeneratedMedia(
        tasks,
        media_objects=InMemoryMediaObjects({temporary_key}),
        storage_allowances=InMemoryStorageAllowances({"account-space-1": 100}),
        id_factory=lambda: "media-1",
    )
    generated_media.register(
        GeneratedMediaRegistration(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            result_reference="result-1",
            object_key=temporary_key,
            kind="image",
            mime_type="image/png",
            size_bytes=100,
            content_hash="a" * 64,
            created_at=now,
        )
    )
    assets = InMemoryPersonalAssets(generated_media, id_factory=lambda: "asset-1")
    saved = assets.save_generated_media(
        PersonalAssetSave(
            user_id="user-1",
            account_space_id="account-space-1",
            media_id="media-1",
            display_name="角色立绘",
            idempotency_key="save-result-1",
            saved_at=now,
        )
    )

    renamed = assets.rename(
        PersonalAssetRename(
            account_space_id="account-space-1",
            asset_id="asset-1",
            display_name="  主角立绘  ",
        )
    )

    assert renamed == replace(saved, display_name="主角立绘")
    assert assets.list("account-space-1") == (renamed,)


def test_removing_the_only_personal_asset_releases_its_media() -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
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
            title="资产来源画布",
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
    temporary_key = "temporary/account-space-1/task-1/removable.png"
    generated_media = InMemoryGeneratedMedia(
        tasks,
        media_objects=InMemoryMediaObjects({temporary_key}),
        storage_allowances=InMemoryStorageAllowances({"account-space-1": 100}),
        id_factory=lambda: "media-1",
    )
    generated_media.register(
        GeneratedMediaRegistration(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            result_reference="result-1",
            object_key=temporary_key,
            kind="image",
            mime_type="image/png",
            size_bytes=100,
            content_hash="a" * 64,
            created_at=now,
        )
    )
    assets = InMemoryPersonalAssets(generated_media, id_factory=lambda: "asset-1")
    assets.save_generated_media(
        PersonalAssetSave(
            user_id="user-1",
            account_space_id="account-space-1",
            media_id="media-1",
            display_name="待移除立绘",
            idempotency_key="save-removable-result",
            saved_at=now,
        )
    )
    removed_at = now + timedelta(hours=1)

    assets.remove("account-space-1", "asset-1", removed_at)

    assert assets.list("account-space-1") == ()
    released = generated_media.get("account-space-1", "media-1")
    assert released.state is GeneratedMediaState.RELEASED
    assert released.released_at == removed_at


def test_failed_personal_asset_object_deletion_stays_hidden_and_can_be_retried() -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
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
            title="资产来源画布",
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
    temporary_key = "temporary/account-space-1/task-1/retry.png"
    generated_media = InMemoryGeneratedMedia(
        tasks,
        media_objects=_FailOnceOnDeleteMediaObjects({temporary_key}),
        storage_allowances=InMemoryStorageAllowances({"account-space-1": 100}),
        id_factory=lambda: "media-1",
    )
    generated_media.register(
        GeneratedMediaRegistration(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            result_reference="result-1",
            object_key=temporary_key,
            kind="image",
            mime_type="image/png",
            size_bytes=100,
            content_hash="a" * 64,
            created_at=now,
        )
    )
    assets = InMemoryPersonalAssets(generated_media, id_factory=lambda: "asset-1")
    assets.save_generated_media(
        PersonalAssetSave(
            user_id="user-1",
            account_space_id="account-space-1",
            media_id="media-1",
            display_name="待重试移除资产",
            idempotency_key="save-retry-result",
            saved_at=now,
        )
    )

    with pytest.raises(MediaObjectDeletionFailed):
        assets.remove("account-space-1", "asset-1", now)

    assert assets.list("account-space-1") == ()
    assert generated_media.get("account-space-1", "media-1").state is GeneratedMediaState.PERSISTENT

    retried_at = now + timedelta(minutes=1)
    assets.remove("account-space-1", "asset-1", retried_at)

    released = generated_media.get("account-space-1", "media-1")
    assert released.state is GeneratedMediaState.RELEASED
    assert released.released_at == retried_at
