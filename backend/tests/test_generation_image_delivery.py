import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.canvases import CanvasCreation, InMemoryCanvases
from app.credits import InMemoryCredits
from app.generation import (
    GenerationCancelled,
    GenerationParameters,
    GenerationStarted,
    GenerationSubmission,
    GenerationTaskStatus,
    InMemoryGenerationTasks,
)
from app.generation_results import (
    GenerationImageContent,
    GenerationImageDelivery,
    InvalidGenerationOutputBatch,
)
from app.media import (
    FileSystemMediaObjects,
    InMemoryGeneratedMedia,
    InMemoryStorageAllowances,
    StoredMediaObject,
)

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _delivery_context(
    tmp_path: Path,
    *,
    quantity: int = 1,
) -> tuple[
    datetime,
    InMemoryGenerationTasks,
    FileSystemMediaObjects,
    InMemoryGeneratedMedia,
    InMemoryCredits,
]:
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
            title="缁撴灉鐢诲竷",
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
            prompt="娴嬭瘯鐢熸垚璇锋眰",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
        )
    )
    tasks.transition(
        "account-space-1",
        "task-1",
        GenerationStarted(provider_task_id="provider-task-1", occurred_at=now),
    )
    objects = FileSystemMediaObjects(tmp_path / "generated-media")
    media_ids = (f"media-{index}" for index in range(1, quantity + 2))
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=objects,
        storage_allowances=InMemoryStorageAllowances({}),
        id_factory=media_ids.__next__,
    )
    return now, tasks, objects, media, credits


def test_cancelled_task_rejects_late_provider_images_before_storage(tmp_path: Path) -> None:
    now, tasks, objects, media, credits = _delivery_context(tmp_path)
    tasks.transition(
        "account-space-1",
        "task-1",
        GenerationCancelled(
            reason="平台管理员手动取消任务",
            outcome_reference="cancel:account-space-1:task-1",
            occurred_at=now + timedelta(minutes=1),
        ),
    )

    with pytest.raises(InvalidGenerationOutputBatch, match="任务已取消"):
        GenerationImageDelivery(tasks, media, objects).receive(
            "account-space-1",
            "task-1",
            (
                GenerationImageContent(
                    result_reference="late-result-1",
                    mime_type="image/png",
                    content=_PNG_BYTES,
                ),
            ),
            completed_at=now + timedelta(minutes=2),
        )

    assert media.list_for_task("account-space-1", "task-1") == ()
    assert credits.statement("account-space-1").available_credits == "1.0000"


class _FailOnceOnSecondImage:
    def __init__(self, delegate: FileSystemMediaObjects) -> None:
        self._delegate = delegate
        self._failed = False

    def put_temporary(
        self,
        *,
        account_space_id: str,
        task_id: str,
        result_reference: str,
        content: bytes,
        mime_type: str,
    ) -> StoredMediaObject:
        if result_reference == "result-2" and not self._failed:
            self._failed = True
            raise OSError("simulated storage interruption")
        return self._delegate.put_temporary(
            account_space_id=account_space_id,
            task_id=task_id,
            result_reference=result_reference,
            content=content,
            mime_type=mime_type,
        )

    def read(self, object_key: str) -> bytes:
        return self._delegate.read(object_key)

    def delete(self, object_key: str) -> None:
        self._delegate.delete(object_key)

    def promote(self, temporary_key: str, persistent_key: str) -> None:
        self._delegate.promote(temporary_key, persistent_key)


def test_image_content_delivery_stores_registers_and_finalizes_the_task(tmp_path: Path) -> None:
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
    objects = FileSystemMediaObjects(tmp_path / "generated-media")
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=objects,
        storage_allowances=InMemoryStorageAllowances({}),
        id_factory=lambda: "media-1",
    )

    finalized = GenerationImageDelivery(tasks, media, objects).receive(
        "account-space-1",
        "task-1",
        (
            GenerationImageContent(
                result_reference="result-1",
                mime_type="image/png",
                content=_PNG_BYTES,
            ),
        ),
        completed_at=now,
    )

    registered = media.list_for_task("account-space-1", "task-1")
    assert finalized.status is GenerationTaskStatus.SUCCEEDED
    assert finalized.delivered_quantity == 1
    assert tuple(item.media_id for item in registered) == ("media-1",)
    assert objects.read(registered[0].object_key) == _PNG_BYTES
    assert tuple(entry.kind for entry in credits.statement("account-space-1").entries).count("settlement") == 1


@pytest.mark.parametrize(
    ("mime_type", "content"),
    [
        ("application/octet-stream", _PNG_BYTES),
        ("image/png", b"not-a-png"),
        ("image/jpeg", b"not-a-jpeg"),
        ("image/webp", b"not-a-webp"),
        ("image/png", b""),
    ],
)
def test_image_delivery_rejects_invalid_image_bytes_before_writing(
    tmp_path: Path,
    mime_type: str,
    content: bytes,
) -> None:
    now, tasks, objects, media, _ = _delivery_context(tmp_path)

    with pytest.raises(InvalidGenerationOutputBatch):
        GenerationImageDelivery(tasks, media, objects).receive(
            "account-space-1",
            "task-1",
            (GenerationImageContent("result-1", mime_type, content),),
            completed_at=now,
        )

    assert not (tmp_path / "generated-media").exists()
    assert media.list_for_task("account-space-1", "task-1") == ()


def test_interrupted_image_batch_replays_without_duplicate_media(
    tmp_path: Path,
) -> None:
    now, tasks, objects, media, _ = _delivery_context(tmp_path, quantity=2)
    interrupted = _FailOnceOnSecondImage(objects)
    delivery = GenerationImageDelivery(tasks, media, interrupted)
    batch = (
        GenerationImageContent("result-1", "image/png", _PNG_BYTES),
        GenerationImageContent("result-2", "image/png", _PNG_BYTES),
    )

    with pytest.raises(OSError, match="storage interruption"):
        delivery.receive("account-space-1", "task-1", batch, completed_at=now)

    assert tasks.get("account-space-1", "task-1").status is GenerationTaskStatus.RUNNING
    assert media.list_for_task("account-space-1", "task-1") == ()

    completed = delivery.receive("account-space-1", "task-1", batch, completed_at=now)
    replay = delivery.receive("account-space-1", "task-1", batch, completed_at=now)

    registered = media.list_for_task("account-space-1", "task-1")
    assert completed.status is GenerationTaskStatus.SUCCEEDED
    assert replay == completed
    assert tuple(item.result_reference for item in registered) == ("result-1", "result-2")
    assert all(objects.read(item.object_key) == _PNG_BYTES for item in registered)


def test_image_delivery_rejects_a_three_minute_late_result_before_writing(tmp_path: Path) -> None:
    now, tasks, objects, media, credits = _delivery_context(tmp_path)

    with pytest.raises(InvalidGenerationOutputBatch, match="超过管理员设置"):
        GenerationImageDelivery(tasks, media, objects).receive(
            "account-space-1",
            "task-1",
            (GenerationImageContent("result-1", "image/png", _PNG_BYTES),),
            completed_at=now + timedelta(minutes=20),
        )

    task = tasks.get("account-space-1", "task-1")
    assert task.status is GenerationTaskStatus.FAILED
    assert task.error == "generation task exceeded configured deadline"
    assert credits.statement("account-space-1").available_credits == "1.0000"
    assert credits.statement("account-space-1").frozen_credits == "0.0000"
    assert not (tmp_path / "generated-media").exists()
    assert media.list_for_task("account-space-1", "task-1") == ()
