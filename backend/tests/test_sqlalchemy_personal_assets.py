from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from app.accounts import SqlAlchemyAccountAccess
from app.assets import PersonalAssetRename, PersonalAssetSave, SqlAlchemyPersonalAssets
from app.canvases import CanvasCreation, SqlAlchemyCanvases
from app.credits import SqlAlchemyCredits
from app.generation import GenerationParameters, GenerationStarted, GenerationSubmission, SqlAlchemyGenerationTasks
from app.media import (
    GeneratedMediaRegistration,
    GeneratedMediaState,
    InMemoryMediaObjects,
    InMemoryStorageAllowances,
    MediaObjectDeletionFailed,
    SqlAlchemyGeneratedMedia,
)


class _FailOnceOnSelectedDeleteMediaObjects:
    def __init__(self, object_keys: set[str], failed_object_key: str) -> None:
        self._delegate = InMemoryMediaObjects(object_keys)
        self._failed_object_key = failed_object_key
        self._delete_should_fail = True

    def delete(self, object_key: str) -> None:
        if object_key == self._failed_object_key and self._delete_should_fail:
            self._delete_should_fail = False
            raise MediaObjectDeletionFailed(object_key)
        self._delegate.delete(object_key)

    def promote(self, temporary_key: str, persistent_key: str) -> None:
        self._delegate.promote(temporary_key, persistent_key)


def test_sqlalchemy_personal_asset_survives_restart_and_idempotent_replay(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'personal-assets.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    now = datetime(2027, 8, 10, 12, 0, tzinfo=UTC)
    owner = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "assets@example.com", "a-correct-horse-battery-staple"
    )
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        owner.account_space_id,
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    canvases = SqlAlchemyCanvases.for_database_url(database_url, id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=owner.user_id,
            account_space_id=owner.account_space_id,
            title="资产来源画布",
            kind="classic",
            created_at=now,
        )
    )
    tasks = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=credits,
        canvases=canvases,
        max_active_tasks=2,
    )
    tasks.submit(
        GenerationSubmission(
            user_id=owner.user_id,
            account_space_id=owner.account_space_id,
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
        owner.account_space_id,
        "task-1",
        GenerationStarted(provider_task_id="provider-task-1", occurred_at=now),
    )
    temporary_key = "temporary/assets/task-1/result.png"
    second_temporary_key = "temporary/assets/task-1/result-2.png"
    media_objects = _FailOnceOnSelectedDeleteMediaObjects(
        {temporary_key, second_temporary_key},
        f"persistent/{owner.account_space_id}/{'b' * 64}",
    )
    allowances = InMemoryStorageAllowances({owner.account_space_id: 100})
    generated_media = SqlAlchemyGeneratedMedia.for_database_url(
        database_url,
        generation_tasks=tasks,
        media_objects=media_objects,
        storage_allowances=allowances,
        id_factory=iter(("media-1", "media-2")).__next__,
    )
    generated_media.register(
        GeneratedMediaRegistration(
            user_id=owner.user_id,
            account_space_id=owner.account_space_id,
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
    generated_media.retain_to_canvas(owner.account_space_id, "media-1", now)
    assets = SqlAlchemyPersonalAssets.for_database_url(
        database_url,
        generated_media=generated_media,
        id_factory=lambda: "asset-1",
    )
    save = PersonalAssetSave(
        user_id=owner.user_id,
        account_space_id=owner.account_space_id,
        media_id="media-1",
        display_name="角色立绘",
        idempotency_key="save-result-1",
        saved_at=now,
    )

    saved = assets.save_generated_media(save)
    renamed = assets.rename(
        PersonalAssetRename(
            account_space_id=owner.account_space_id,
            asset_id=saved.asset_id,
            display_name="  主角立绘  ",
        )
    )
    restarted = SqlAlchemyPersonalAssets.for_database_url(database_url, generated_media=generated_media)

    assert renamed == replace(saved, display_name="主角立绘")
    assert restarted.list(owner.account_space_id) == (renamed,)
    assert restarted.save_generated_media(replace(save, saved_at=now + timedelta(seconds=1))) == renamed
    assert generated_media.get(owner.account_space_id, "media-1").state is GeneratedMediaState.PERSISTENT

    removed_at = now + timedelta(hours=1)
    restarted.remove(owner.account_space_id, "asset-1", removed_at)

    after_removal = SqlAlchemyPersonalAssets.for_database_url(database_url, generated_media=generated_media)
    assert after_removal.list(owner.account_space_id) == ()
    assert generated_media.get(owner.account_space_id, "media-1").state is GeneratedMediaState.PERSISTENT

    result = generated_media.reconcile_canvas_references(owner.account_space_id, "canvas-1", (), removed_at)

    assert result.released_media_ids == ("media-1",)
    assert generated_media.get(owner.account_space_id, "media-1").state is GeneratedMediaState.RELEASED
    after_removal.remove(owner.account_space_id, "asset-1", removed_at + timedelta(seconds=1))

    generated_media.register(
        GeneratedMediaRegistration(
            user_id=owner.user_id,
            account_space_id=owner.account_space_id,
            canvas_id="canvas-1",
            task_id="task-1",
            result_reference="result-2",
            object_key=second_temporary_key,
            kind="image",
            mime_type="image/png",
            size_bytes=100,
            content_hash="b" * 64,
            created_at=now,
        )
    )
    second_assets = SqlAlchemyPersonalAssets.for_database_url(
        database_url,
        generated_media=generated_media,
        id_factory=lambda: "asset-2",
    )
    second_assets.save_generated_media(
        PersonalAssetSave(
            user_id=owner.user_id,
            account_space_id=owner.account_space_id,
            media_id="media-2",
            display_name="第二项资产",
            idempotency_key="save-result-2",
            saved_at=now,
        )
    )

    with pytest.raises(MediaObjectDeletionFailed):
        second_assets.remove(owner.account_space_id, "asset-2", removed_at)

    assert second_assets.list(owner.account_space_id) == ()
    assert generated_media.get(owner.account_space_id, "media-2").state is GeneratedMediaState.PERSISTENT

    second_assets.remove(owner.account_space_id, "asset-2", removed_at + timedelta(seconds=1))

    assert generated_media.get(owner.account_space_id, "media-2").state is GeneratedMediaState.RELEASED
