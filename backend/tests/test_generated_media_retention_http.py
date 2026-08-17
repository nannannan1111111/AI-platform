from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.canvases import CanvasCreation, InMemoryCanvases
from app.credits import InMemoryCredits
from app.generation import GenerationParameters, GenerationStarted, GenerationSubmission, InMemoryGenerationTasks
from app.http import create_app
from app.media import (
    GeneratedMediaRegistration,
    InMemoryGeneratedMedia,
    InMemoryMediaObjects,
    InMemoryStorageAllowances,
)


def test_owner_explicitly_retains_temporary_media_to_its_original_canvas() -> None:
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
            title="持久媒体画布",
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
    temporary_key = "temporary/owner/task-1/result-1.png"
    generated_media = InMemoryGeneratedMedia(
        tasks,
        media_objects=InMemoryMediaObjects({temporary_key}),
        storage_allowances=InMemoryStorageAllowances({owner.account_space_id: 1000}),
        id_factory=lambda: "media-1",
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
    client = TestClient(create_app(accounts, generated_media=generated_media, clock=lambda: now))
    owner_headers = {"Authorization": f"Bearer {owner_session.access_token}"}
    other_headers = {"Authorization": f"Bearer {other_session.access_token}"}

    response = client.post("/api/v1/media/media-1/retain-to-canvas", headers=owner_headers)
    replay = client.post("/api/v1/media/media-1/retain-to-canvas", headers=owner_headers)

    assert response.status_code == 200
    retained = response.json()
    assert retained == {
        "media_id": "media-1",
        "task_id": "task-1",
        "kind": "image",
        "mime_type": "image/png",
        "size_bytes": 100,
        "state": "persistent",
        "created_at": "2026-08-10T10:00:00Z",
        "expires_at": None,
        "retained_at": "2026-08-10T10:00:00Z",
    }
    assert replay.json() == retained
    assert client.get("/api/v1/media/media-1", headers=owner_headers).json() == retained
    assert client.post("/api/v1/media/media-1/retain-to-canvas", headers=other_headers).status_code == 404
    assert client.get("/api/v1/auth/me", headers=owner_headers).json()["storage_allowance"] == {
        "limit_bytes": 1000,
        "used_bytes": 100,
        "available_bytes": 900,
    }


def test_saving_a_canvas_releases_media_removed_from_its_document() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    owner = accounts.register("owner@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("owner@example.com", "a-correct-horse-battery-staple")
    headers = {"Authorization": f"Bearer {session.access_token}"}
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
            title="移除媒体的画布",
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
    temporary_key = "temporary/owner/task-1/result-1.png"
    generated_media = InMemoryGeneratedMedia(
        tasks,
        media_objects=InMemoryMediaObjects({temporary_key}),
        storage_allowances=InMemoryStorageAllowances({owner.account_space_id: 1000}),
        id_factory=lambda: "media-1",
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
    client = TestClient(
        create_app(
            accounts,
            canvases=canvases,
            generated_media=generated_media,
            clock=lambda: now,
        )
    )
    first_save = client.put(
        "/api/v1/canvases/canvas-1",
        headers=headers,
        json={
            "expected_version": 1,
            "document": {
                "nodes": [{"id": "node-1", "media_id": "media-1"}],
                "connections": [],
                "viewport": {"x": 0, "y": 0, "scale": 1},
            },
        },
    )

    response = client.put(
        "/api/v1/canvases/canvas-1",
        headers=headers,
        json={
            "expected_version": 2,
            "document": {
                "nodes": [],
                "connections": [],
                "viewport": {"x": 0, "y": 0, "scale": 1},
            },
        },
    )

    assert first_save.status_code == 200
    assert response.status_code == 200
    released = client.get("/api/v1/media/media-1", headers=headers).json()
    assert released["state"] == "released"
    assert client.post("/api/v1/media/media-1/retain-to-canvas", headers=headers).status_code == 409
