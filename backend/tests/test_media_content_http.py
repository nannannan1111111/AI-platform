import base64
import io
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.accounts import InMemoryAccountAccess
from app.canvases import CanvasCreation, InMemoryCanvases
from app.credits import InMemoryCredits
from app.generation import GenerationParameters, GenerationStarted, GenerationSubmission, InMemoryGenerationTasks
from app.generation_results import GenerationImageContent, GenerationImageDelivery
from app.http import create_app
from app.media import (
    FileSystemMediaObjects,
    GeneratedMediaRegistration,
    InMemoryGeneratedMedia,
    InMemoryStorageAllowances,
)
from app.reference_media import InMemoryReferenceMedia

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
_WEBP_BYTES = b"RIFF\x0c\x00\x00\x00WEBPVP8 \x00\x00\x00\x00"


def test_owner_downloads_selected_generated_images_as_a_numbered_zip(tmp_path: Path) -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    owner = accounts.register("owner@example.com", "a-correct-horse-battery-staple")
    accounts.register("other@example.com", "a-correct-horse-battery-staple")
    owner_session = accounts.login("owner@example.com", "a-correct-horse-battery-staple")
    other_session = accounts.login("other@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={owner.account_space_id})
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        owner.account_space_id,
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    canvases = InMemoryCanvases(id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=owner.user_id,
            account_space_id=owner.account_space_id,
            title="结果画布",
            kind="classic",
            created_at=now,
        )
    )
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    tasks.submit(
        GenerationSubmission(
            user_id=owner.user_id,
            account_space_id=owner.account_space_id,
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
        owner.account_space_id,
        "task-1",
        GenerationStarted(provider_task_id="provider-task-1", occurred_at=now),
    )
    objects = FileSystemMediaObjects(tmp_path / "generated-media")
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=objects,
        storage_allowances=InMemoryStorageAllowances({}),
        id_factory=iter(("media-1", "media-2")).__next__,
    )
    for result_reference, mime_type, content in (
        ("result-1", "image/png", _PNG_BYTES),
        ("result-2", "image/webp", _WEBP_BYTES),
    ):
        stored = objects.put_temporary(
            account_space_id=owner.account_space_id,
            task_id="task-1",
            result_reference=result_reference,
            content=content,
            mime_type=mime_type,
        )
        media.register(
            GeneratedMediaRegistration(
                user_id=owner.user_id,
                account_space_id=owner.account_space_id,
                canvas_id="canvas-1",
                task_id="task-1",
                result_reference=result_reference,
                object_key=stored.object_key,
                kind="image",
                mime_type=mime_type,
                size_bytes=stored.size_bytes,
                content_hash=stored.content_hash,
                created_at=now,
            )
        )
    client = TestClient(
        create_app(
            accounts,
            generation_tasks=tasks,
            generated_media=media,
            media_content=objects,
            reference_media=InMemoryReferenceMedia(id_factory=lambda: "reference-from-result"),
            clock=lambda: now,
        )
    )
    owner_headers = {"Authorization": f"Bearer {owner_session.access_token}"}

    response = client.post(
        "/api/v1/media/archive",
        headers=owner_headers,
        json={"media_ids": ["media-1", "media-2"]},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["20260810-100000-01.png", "20260810-100000-02.webp"]
        assert archive.read(archive.namelist()[0]) == _PNG_BYTES
        assert archive.read(archive.namelist()[1]) == _WEBP_BYTES
    assert (
        client.post(
            "/api/v1/media/archive",
            headers={"Authorization": f"Bearer {other_session.access_token}"},
            json={"media_ids": ["media-1"]},
        ).status_code
        == 404
    )

    reference = client.post("/api/v1/media/media-1/use-as-reference", headers=owner_headers)

    assert reference.status_code == 201
    assert reference.json()["media_id"] == "reference-from-result"
    assert client.get("/api/v1/reference-media/recent", headers=owner_headers).json() == []
    preview = client.get(reference.json()["preview_url"], headers=owner_headers)
    assert preview.content == _PNG_BYTES
    assert (
        client.post(
            "/api/v1/media/media-1/use-as-reference",
            headers={"Authorization": f"Bearer {other_session.access_token}"},
        ).status_code
        == 404
    )


def test_owner_reads_generated_image_content_until_exact_24_hour_expiration(tmp_path: Path) -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    current_time = [now]
    accounts = InMemoryAccountAccess(clock=lambda: current_time[0])
    owner = accounts.register("owner@example.com", "a-correct-horse-battery-staple")
    accounts.register("other@example.com", "a-correct-horse-battery-staple")
    owner_session = accounts.login("owner@example.com", "a-correct-horse-battery-staple")
    other_session = accounts.login("other@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={owner.account_space_id})
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        owner.account_space_id,
        package.version_id,
        payment_reference="payment-1",
        occurred_at=now,
    )
    canvases = InMemoryCanvases(id_factory=lambda: "canvas-1")
    canvases.create(
        CanvasCreation(
            user_id=owner.user_id,
            account_space_id=owner.account_space_id,
            title="结果画布",
            kind="classic",
            created_at=now,
        )
    )
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
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
    objects = FileSystemMediaObjects(tmp_path / "generated-media")
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=objects,
        storage_allowances=InMemoryStorageAllowances({}),
        id_factory=lambda: "media-1",
    )
    GenerationImageDelivery(tasks, media, objects).receive(
        owner.account_space_id,
        "task-1",
        (GenerationImageContent("result-1", "image/png", _PNG_BYTES),),
        completed_at=now,
    )
    client = TestClient(
        create_app(
            accounts,
            generation_tasks=tasks,
            generated_media=media,
            media_content=objects,
            clock=lambda: current_time[0],
        )
    )
    owner_headers = {"Authorization": f"Bearer {owner_session.access_token}"}
    other_headers = {"Authorization": f"Bearer {other_session.access_token}"}

    response = client.get("/api/v1/media/media-1/content", headers=owner_headers)

    assert response.status_code == 200
    assert response.content == _PNG_BYTES
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, no-store"
    assert "temporary" not in response.headers.get("content-location", "")
    assert client.get("/api/v1/media/media-1/content", headers=other_headers).status_code == 404

    thumbnail = client.get("/api/v1/media/media-1/thumbnail?size=64", headers=owner_headers)
    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"] == "image/webp"
    assert thumbnail.headers["cache-control"] == "private, no-store"
    with Image.open(io.BytesIO(thumbnail.content)) as preview:
        assert preview.size == (1, 1)
    assert client.get("/api/v1/media/media-1/thumbnail?size=64", headers=other_headers).status_code == 404
    assert client.get("/api/v1/media/media-1/thumbnail?size=32", headers=owner_headers).status_code == 422

    registered = media.get(owner.account_space_id, "media-1")
    current_time[0] = now + timedelta(hours=24)
    refreshed_session = accounts.login("owner@example.com", "a-correct-horse-battery-staple")
    refreshed_headers = {"Authorization": f"Bearer {refreshed_session.access_token}"}

    assert client.get("/api/v1/media/media-1/content", headers=refreshed_headers).status_code == 404
    assert client.get("/api/v1/media/media-1/thumbnail?size=64", headers=refreshed_headers).status_code == 404
    with pytest.raises(FileNotFoundError):
        objects.read(registered.object_key)
