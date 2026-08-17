from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from app.accounts import SqlAlchemyAccountAccess
from app.canvases import CanvasCreation, InMemoryCanvases, SqlAlchemyCanvases
from app.credits import InMemoryCredits, SqlAlchemyCredits
from app.generation import (
    GenerationParameters,
    GenerationStarted,
    GenerationSubmission,
    GenerationTaskStatus,
    InMemoryGenerationTasks,
    SqlAlchemyGenerationTasks,
)
from app.generation_results import (
    GenerationOutput,
    GenerationOutputReceiver,
    GenerationResultFinalizer,
    InvalidGenerationOutputBatch,
    InvalidGenerationResult,
)
from app.media import (
    GeneratedMediaRegistration,
    InMemoryGeneratedMedia,
    InMemoryMediaObjects,
    InMemoryStorageAllowances,
    SqlAlchemyGeneratedMedia,
)


def test_receiver_registers_complete_output_batch_and_finalizes_task() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    finished_at = datetime(2026, 8, 10, 10, 5, tzinfo=UTC)
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
            title="结果画布",
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
            quantity=2,
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
        media_objects=InMemoryMediaObjects({"temporary/result-1.png", "temporary/result-2.png"}),
        storage_allowances=InMemoryStorageAllowances({}),
        id_factory=iter(("media-1", "media-2")).__next__,
    )
    receiver = GenerationOutputReceiver(tasks, media)

    finalized = receiver.receive(
        "account-space-1",
        "task-1",
        (
            GenerationOutput(
                result_reference="result-1",
                object_key="temporary/result-1.png",
                mime_type="image/png",
                size_bytes=1234,
                content_hash="a" * 64,
            ),
            GenerationOutput(
                result_reference="result-2",
                object_key="temporary/result-2.png",
                mime_type="image/png",
                size_bytes=2345,
                content_hash="b" * 64,
            ),
        ),
        completed_at=finished_at,
    )

    registered = media.list_for_task("account-space-1", "task-1")
    assert tuple(item.result_reference for item in registered) == ("result-1", "result-2")
    assert finalized.status is GenerationTaskStatus.SUCCEEDED
    assert finalized.delivered_quantity == 2
    assert tuple(entry.kind for entry in credits.statement("account-space-1").entries).count("settlement") == 1


def test_receiver_rejects_cumulative_over_delivery_before_registering_new_output() -> None:
    tasks, credits, media, now = _running_task_with_registered_images(quantity=1, media_count=1)
    receiver = GenerationOutputReceiver(tasks, media)

    with pytest.raises(InvalidGenerationOutputBatch):
        receiver.receive(
            "account-space-1",
            "task-1",
            (
                GenerationOutput(
                    result_reference="result-new",
                    object_key="temporary/result-new.png",
                    mime_type="image/png",
                    size_bytes=1234,
                    content_hash="f" * 64,
                ),
            ),
            completed_at=now,
        )

    assert tuple(item.result_reference for item in media.list_for_task("account-space-1", "task-1")) == ("result-0",)
    assert tasks.get("account-space-1", "task-1").status is GenerationTaskStatus.RUNNING
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def test_receiver_rejects_duplicate_batch_references_before_registration() -> None:
    tasks, credits, media, now = _running_task_with_registered_images(quantity=2, media_count=0)
    output = GenerationOutput(
        result_reference="result-1",
        object_key="temporary/result-1.png",
        mime_type="image/png",
        size_bytes=1234,
        content_hash="a" * 64,
    )

    with pytest.raises(InvalidGenerationOutputBatch):
        GenerationOutputReceiver(tasks, media).receive(
            "account-space-1",
            "task-1",
            (output, output),
            completed_at=now,
        )

    assert media.list_for_task("account-space-1", "task-1") == ()
    assert tasks.get("account-space-1", "task-1").status is GenerationTaskStatus.RUNNING
    assert credits.statement("account-space-1").frozen_credits == "0.3000"


def test_receiver_resumes_partially_registered_batch_and_settles_once() -> None:
    tasks, credits, media, now = _running_task_with_registered_images(quantity=2, media_count=1)

    finalized = GenerationOutputReceiver(tasks, media).receive(
        "account-space-1",
        "task-1",
        (
            GenerationOutput(
                result_reference="result-0",
                object_key="temporary/result-0.png",
                mime_type="image/png",
                size_bytes=1234,
                content_hash=f"{1:064x}",
            ),
            GenerationOutput(
                result_reference="result-1",
                object_key="temporary/result-1.png",
                mime_type="image/png",
                size_bytes=2345,
                content_hash=f"{2:064x}",
            ),
        ),
        completed_at=now,
    )

    assert tuple(item.result_reference for item in media.list_for_task("account-space-1", "task-1")) == (
        "result-0",
        "result-1",
    )
    assert finalized.status is GenerationTaskStatus.SUCCEEDED
    assert finalized.delivered_quantity == 2
    assert tuple(entry.kind for entry in credits.statement("account-space-1").entries).count("settlement") == 1


def test_receiver_empty_batch_fails_task_and_releases_once() -> None:
    tasks, credits, media, now = _running_task_with_registered_images(quantity=2, media_count=0)
    receiver = GenerationOutputReceiver(tasks, media)

    finalized = receiver.receive(
        "account-space-1",
        "task-1",
        (),
        completed_at=now,
    )
    replay = receiver.receive(
        "account-space-1",
        "task-1",
        (),
        completed_at=now,
    )

    assert finalized.status is GenerationTaskStatus.FAILED
    assert replay == finalized
    statement = credits.statement("account-space-1")
    assert statement.available_credits == "1.0000"
    assert statement.frozen_credits == "0.0000"
    assert tuple(entry.kind for entry in statement.entries).count("release") == 1


def test_registered_partial_image_delivery_succeeds_and_settles_once() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    finished_at = datetime(2026, 8, 10, 10, 5, tzinfo=UTC)
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
            title="结果画布",
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
            quantity=2,
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
        media_objects=InMemoryMediaObjects({"temporary/result-1.png"}),
        storage_allowances=InMemoryStorageAllowances({}),
        id_factory=lambda: "media-1",
    )
    media.register(
        GeneratedMediaRegistration(
            user_id="user-1",
            account_space_id="account-space-1",
            canvas_id="canvas-1",
            task_id="task-1",
            result_reference="result-1",
            object_key="temporary/result-1.png",
            kind="image",
            mime_type="image/png",
            size_bytes=1234,
            content_hash="a" * 64,
            created_at=now,
        )
    )
    finalizer = GenerationResultFinalizer(tasks, media)

    finalized = finalizer.finalize("account-space-1", "task-1", occurred_at=finished_at)
    replay = finalizer.finalize("account-space-1", "task-1", occurred_at=finished_at)

    assert finalized.status is GenerationTaskStatus.SUCCEEDED
    assert finalized.delivered_quantity == 1
    assert replay == finalized
    statement = credits.statement("account-space-1")
    assert statement.available_credits == "0.8500"
    assert statement.frozen_credits == "0.0000"
    assert tuple(entry.kind for entry in statement.entries).count("settlement") == 1


def test_zero_registered_results_fails_and_releases_once() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    finished_at = datetime(2026, 8, 10, 10, 5, tzinfo=UTC)
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
            title="结果画布",
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
            quantity=2,
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
        media_objects=InMemoryMediaObjects(),
        storage_allowances=InMemoryStorageAllowances({}),
    )
    finalizer = GenerationResultFinalizer(tasks, media)

    finalized = finalizer.finalize("account-space-1", "task-1", occurred_at=finished_at)
    replay = finalizer.finalize("account-space-1", "task-1", occurred_at=finished_at)

    assert finalized.status is GenerationTaskStatus.FAILED
    assert replay == finalized
    statement = credits.statement("account-space-1")
    assert statement.available_credits == "1.0000"
    assert statement.frozen_credits == "0.0000"
    assert tuple(entry.kind for entry in statement.entries).count("release") == 1


def test_queued_task_cannot_be_finalized_as_zero_delivery() -> None:
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
            title="结果画布",
            kind="classic",
            created_at=now,
        )
    )
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    queued = tasks.submit(
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
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=InMemoryMediaObjects(),
        storage_allowances=InMemoryStorageAllowances({}),
    )

    with pytest.raises(InvalidGenerationResult):
        GenerationResultFinalizer(tasks, media).finalize("account-space-1", "task-1", occurred_at=now)

    assert tasks.get("account-space-1", "task-1") == queued
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def test_registered_partial_delivery_survives_sqlalchemy_adapter_restart(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'generation-result-finalization.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    now = datetime(2027, 8, 10, 10, 0, tzinfo=UTC)
    finished_at = datetime(2027, 8, 10, 10, 5, tzinfo=UTC)
    account = SqlAlchemyAccountAccess.for_database_url(database_url).register(
        "result-owner@example.com",
        "a-correct-horse-battery-staple",
    )
    credits = SqlAlchemyCredits.for_database_url(database_url, clock=lambda: now)
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        account.account_space_id,
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    canvases = SqlAlchemyCanvases.for_database_url(database_url, id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=account.user_id,
            account_space_id=account.account_space_id,
            title="结果画布",
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
            user_id=account.user_id,
            account_space_id=account.account_space_id,
            canvas_id="canvas-1",
            task_id="task-1",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=2,
            prompt="测试生成请求",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
        )
    )
    tasks.transition(
        account.account_space_id,
        "task-1",
        GenerationStarted(provider_task_id="provider-task-1", occurred_at=now),
    )
    media_objects = InMemoryMediaObjects({"temporary/result-1.png"})
    allowances = InMemoryStorageAllowances({})
    media = SqlAlchemyGeneratedMedia.for_database_url(
        database_url,
        generation_tasks=tasks,
        media_objects=media_objects,
        storage_allowances=allowances,
        id_factory=lambda: "media-1",
    )
    media.register(
        GeneratedMediaRegistration(
            user_id=account.user_id,
            account_space_id=account.account_space_id,
            canvas_id="canvas-1",
            task_id="task-1",
            result_reference="result-1",
            object_key="temporary/result-1.png",
            kind="image",
            mime_type="image/png",
            size_bytes=1234,
            content_hash="a" * 64,
            created_at=finished_at,
        )
    )
    output = GenerationOutput(
        result_reference="result-1",
        object_key="temporary/result-1.png",
        mime_type="image/png",
        size_bytes=1234,
        content_hash="a" * 64,
    )

    finalized = GenerationOutputReceiver(tasks, media).receive(
        account.account_space_id,
        "task-1",
        (output,),
        completed_at=finished_at,
    )
    restarted_credits = SqlAlchemyCredits.for_database_url(database_url)
    restarted_tasks = SqlAlchemyGenerationTasks.for_database_url(
        database_url,
        credits=restarted_credits,
        canvases=SqlAlchemyCanvases.for_database_url(database_url),
        max_active_tasks=2,
    )
    restarted_media = SqlAlchemyGeneratedMedia.for_database_url(
        database_url,
        generation_tasks=restarted_tasks,
        media_objects=media_objects,
        storage_allowances=allowances,
    )
    replay = GenerationOutputReceiver(restarted_tasks, restarted_media).receive(
        account.account_space_id,
        "task-1",
        (output,),
        completed_at=finished_at,
    )

    assert finalized.status is GenerationTaskStatus.SUCCEEDED
    assert finalized.delivered_quantity == 1
    assert replay == finalized
    statement = restarted_credits.statement(account.account_space_id)
    assert statement.available_credits == "0.8500"
    assert statement.frozen_credits == "0.0000"
    assert tuple(entry.kind for entry in statement.entries).count("settlement") == 1


def test_delivery_cannot_exceed_the_task_requested_quantity() -> None:
    tasks, credits, media, now = _running_task_with_registered_images(quantity=1, media_count=2)

    with pytest.raises(InvalidGenerationResult):
        GenerationResultFinalizer(tasks, media).finalize("account-space-1", "task-1", occurred_at=now)

    assert tasks.get("account-space-1", "task-1").status is GenerationTaskStatus.RUNNING
    assert credits.statement("account-space-1").frozen_credits == "0.1500"


def _running_task_with_registered_images(
    *, quantity: int, media_count: int
) -> tuple[InMemoryGenerationTasks, InMemoryCredits, InMemoryGeneratedMedia, datetime]:
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
            title="结果画布",
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
            quantity=quantity,
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
        media_objects=InMemoryMediaObjects(f"temporary/result-{index}.png" for index in range(media_count)),
        storage_allowances=InMemoryStorageAllowances({}),
        id_factory=iter(f"media-{index}" for index in range(media_count + 10)).__next__,
    )
    for index in range(media_count):
        media.register(
            GeneratedMediaRegistration(
                user_id="user-1",
                account_space_id="account-space-1",
                canvas_id="canvas-1",
                task_id="task-1",
                result_reference=f"result-{index}",
                object_key=f"temporary/result-{index}.png",
                kind="image",
                mime_type="image/png",
                size_bytes=1234,
                content_hash=f"{index + 1:064x}",
                created_at=now,
            )
        )
    return tasks, credits, media, now
