from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from app.accounts import SqlAlchemyAccountAccess
from app.canvases import CanvasCreation, SqlAlchemyCanvases
from app.credits import SqlAlchemyCredits
from app.generation import GenerationParameters, GenerationStarted, GenerationSubmission, SqlAlchemyGenerationTasks
from app.media import (
    GeneratedMediaConflict,
    GeneratedMediaRegistration,
    GeneratedMediaState,
    InMemoryMediaObjects,
    InMemoryStorageAllowances,
    SqlAlchemyGeneratedMedia,
)


class _RecordingMediaObjects:
    def __init__(self, object_keys: set[str]) -> None:
        self.object_keys = object_keys
        self.deleted_keys: list[str] = []

    def delete(self, object_key: str) -> None:
        self.deleted_keys.append(object_key)
        self.object_keys.discard(object_key)


def test_sqlalchemy_standalone_generation_media_survives_restart_without_a_canvas(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'standalone-generated-media.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    now = datetime(2027, 8, 10, 11, 0, tzinfo=UTC)
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "standalone-media@example.com", "a-correct-horse-battery-staple"
    )
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-standalone-media-1",
        occurred_at=now,
    )
    tasks = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=credits,
        canvases=SqlAlchemyCanvases.for_database_url(database_url),
        max_active_tasks=2,
    )
    tasks.submit(
        GenerationSubmission(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            canvas_id=None,
            task_id="standalone-task-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="Standalone result",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
        )
    )
    tasks.transition(
        registration.account_space_id,
        "standalone-task-1",
        GenerationStarted(provider_task_id="provider-task-1", occurred_at=now),
    )
    object_key = "temporary/account-space/standalone-task-1/result-1.png"
    objects = InMemoryMediaObjects({object_key})
    allowances = InMemoryStorageAllowances({registration.account_space_id: 100})
    media = SqlAlchemyGeneratedMedia.for_database_url(
        database_url,
        generation_tasks=tasks,
        media_objects=objects,
        storage_allowances=allowances,
        id_factory=lambda: "media-standalone-1",
    )

    created = media.register(
        GeneratedMediaRegistration(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            canvas_id=None,
            task_id="standalone-task-1",
            result_reference="result-1",
            object_key=object_key,
            kind="image",
            mime_type="image/png",
            size_bytes=100,
            content_hash="b" * 64,
            created_at=now,
        )
    )
    restarted = SqlAlchemyGeneratedMedia.for_database_url(
        database_url,
        generation_tasks=tasks,
        media_objects=objects,
        storage_allowances=allowances,
    )

    assert created.canvas_id is None
    assert restarted.get(registration.account_space_id, created.media_id) == created


def test_sqlalchemy_generated_media_survives_restart_and_replays_by_result_reference(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'generated-media.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    now = datetime.now(UTC) + timedelta(minutes=1)
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "artist@example.com", "a-correct-horse-battery-staple"
    )
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    canvases = SqlAlchemyCanvases.for_database_url(database_url, id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="媒体画布",
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
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
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
        registration.account_space_id,
        "task-1",
        GenerationStarted(provider_task_id="provider-task-1", occurred_at=now),
    )
    command_data = GeneratedMediaRegistration(
        user_id=registration.user_id,
        account_space_id=registration.account_space_id,
        canvas_id="canvas-1",
        task_id="task-1",
        result_reference="result-1",
        object_key="temporary/artist/task-1/result-1.png",
        kind="image",
        mime_type="image/png",
        size_bytes=1234,
        content_hash="a" * 64,
        created_at=now,
    )
    media_objects = InMemoryMediaObjects({"temporary/artist/task-1/result-1.png"})
    storage_allowances = InMemoryStorageAllowances({registration.account_space_id: 1234})
    media = SqlAlchemyGeneratedMedia.for_database_url(
        database_url,
        generation_tasks=tasks,
        media_objects=media_objects,
        storage_allowances=storage_allowances,
        id_factory=lambda: "media-1",
    )

    created = media.register(command_data)
    restarted = SqlAlchemyGeneratedMedia.for_database_url(
        database_url,
        generation_tasks=tasks,
        media_objects=media_objects,
        storage_allowances=storage_allowances,
    )

    assert restarted.get(registration.account_space_id, "media-1") == created
    assert restarted.list_for_task(registration.account_space_id, "task-1") == (created,)
    assert restarted.register(command_data) == created
    with pytest.raises(GeneratedMediaConflict):
        restarted.register(replace(command_data, object_key="temporary/artist/task-1/different.png"))

    retained = restarted.retain_to_canvas(registration.account_space_id, created.media_id, now)
    after_retention_restart = SqlAlchemyGeneratedMedia.for_database_url(
        database_url,
        generation_tasks=tasks,
        media_objects=media_objects,
        storage_allowances=storage_allowances,
    )

    assert retained.state is GeneratedMediaState.PERSISTENT
    assert retained.expires_at is None
    assert after_retention_restart.get(registration.account_space_id, created.media_id) == retained
    assert after_retention_restart.retain_to_canvas(registration.account_space_id, created.media_id, now) == retained
    assert after_retention_restart.register(command_data) == retained
    allowance = after_retention_restart.storage_allowance(registration.account_space_id)
    assert allowance.limit_bytes == 1234
    assert allowance.used_bytes == 1234
    assert allowance.available_bytes == 0


def test_sqlalchemy_expiration_survives_adapter_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'expired-media.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    now = datetime.now(UTC) + timedelta(minutes=1)
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "expiration@example.com", "a-correct-horse-battery-staple"
    )
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    canvases = SqlAlchemyCanvases.for_database_url(database_url, id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="到期媒体画布",
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
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
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
        registration.account_space_id,
        "task-1",
        GenerationStarted(provider_task_id="provider-task-1", occurred_at=now),
    )
    objects = _RecordingMediaObjects({"temporary/expiring.png"})
    storage_allowances = InMemoryStorageAllowances({registration.account_space_id: 0})
    media = SqlAlchemyGeneratedMedia.for_database_url(
        database_url,
        generation_tasks=tasks,
        media_objects=objects,
        storage_allowances=storage_allowances,
        id_factory=lambda: "media-1",
    )
    created = media.register(
        GeneratedMediaRegistration(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            canvas_id="canvas-1",
            task_id="task-1",
            result_reference="result-1",
            object_key="temporary/expiring.png",
            kind="image",
            mime_type="image/png",
            size_bytes=1234,
            content_hash="a" * 64,
            created_at=now,
        )
    )

    report = media.expire_due(now + timedelta(hours=24))
    restarted = SqlAlchemyGeneratedMedia.for_database_url(
        database_url,
        generation_tasks=tasks,
        media_objects=objects,
        storage_allowances=storage_allowances,
    )

    assert report.expired_media_ids == (created.media_id,)
    assert objects.deleted_keys == ["temporary/expiring.png"]
    assert restarted.get(registration.account_space_id, created.media_id).state is GeneratedMediaState.EXPIRED


def test_sqlalchemy_released_media_survives_adapter_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'released-media.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    now = datetime(2027, 8, 10, 10, 0, tzinfo=UTC)
    registration = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "release@example.com", "a-correct-horse-battery-staple"
    )
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        registration.account_space_id,
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    canvases = SqlAlchemyCanvases.for_database_url(database_url, id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
            title="释放媒体画布",
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
            user_id=registration.user_id,
            account_space_id=registration.account_space_id,
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
        registration.account_space_id,
        "task-1",
        GenerationStarted(provider_task_id="provider-task-1", occurred_at=now),
    )
    object_key = "temporary/release/result.png"
    media_objects = InMemoryMediaObjects({object_key})
    storage_allowances = InMemoryStorageAllowances({registration.account_space_id: 100})
    media = SqlAlchemyGeneratedMedia.for_database_url(
        database_url,
        generation_tasks=tasks,
        media_objects=media_objects,
        storage_allowances=storage_allowances,
        id_factory=lambda: "media-1",
    )
    media_registration = GeneratedMediaRegistration(
        user_id=registration.user_id,
        account_space_id=registration.account_space_id,
        canvas_id="canvas-1",
        task_id="task-1",
        result_reference="result-1",
        object_key=object_key,
        kind="image",
        mime_type="image/png",
        size_bytes=100,
        content_hash="a" * 64,
        created_at=now,
    )
    created = media.register(media_registration)
    media.retain_to_canvas(registration.account_space_id, created.media_id, now)
    released_at = now + timedelta(hours=1)

    result = media.reconcile_canvas_references(
        registration.account_space_id,
        "canvas-1",
        (),
        released_at,
    )
    restarted = SqlAlchemyGeneratedMedia.for_database_url(
        database_url,
        generation_tasks=tasks,
        media_objects=media_objects,
        storage_allowances=storage_allowances,
    )

    assert result.released_media_ids == ("media-1",)
    released = restarted.get(registration.account_space_id, created.media_id)
    assert released.state is GeneratedMediaState.RELEASED
    assert released.released_at == released_at
    assert restarted.register(media_registration) == released
