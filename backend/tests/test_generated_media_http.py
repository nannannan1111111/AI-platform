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


def test_running_task_media_has_an_account_isolated_user_safe_projection() -> None:
    now = datetime(2026, 8, 8, 16, 0, tzinfo=UTC)
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
            title="媒体画布",
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
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=InMemoryMediaObjects(),
        storage_allowances=InMemoryStorageAllowances({}),
        id_factory=lambda: "media-1",
    )
    registered = media.register(
        GeneratedMediaRegistration(
            user_id=owner.user_id,
            account_space_id=owner.account_space_id,
            canvas_id="canvas-1",
            task_id="task-1",
            result_reference="result-1",
            object_key="temporary/owner/task-1/result-1.png",
            kind="image",
            mime_type="image/png",
            size_bytes=1234,
            content_hash="a" * 64,
            created_at=now,
        )
    )
    client = TestClient(create_app(accounts, generated_media=media))
    owner_headers = {"Authorization": f"Bearer {owner_session.access_token}"}
    other_headers = {"Authorization": f"Bearer {other_session.access_token}"}

    response = client.get("/api/v1/generation-tasks/task-1/media", headers=owner_headers)

    assert response.status_code == 200
    assert response.json() == [
        {
            "media_id": "media-1",
            "task_id": "task-1",
            "kind": "image",
            "mime_type": "image/png",
            "size_bytes": 1234,
            "state": "temporary",
            "created_at": "2026-08-08T16:00:00Z",
            "expires_at": "2026-08-09T16:00:00Z",
            "retained_at": None,
        }
    ]
    assert client.get("/api/v1/media/media-1", headers=owner_headers).json() == response.json()[0]
    assert client.get("/api/v1/generation-tasks/task-1/media", headers=other_headers).status_code == 404
    assert client.get("/api/v1/media/media-1", headers=other_headers).status_code == 404
    assert registered.media_id == "media-1"


def test_owner_can_delete_an_unreferenced_temporary_result_and_release_storage() -> None:
    now = datetime(2026, 8, 8, 16, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    owner = accounts.register("delete-result@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("delete-result@example.com", "a-correct-horse-battery-staple")
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={owner.account_space_id})
    package = credits.publish("starter", payment_cny="1.00", credits="1.0000", effective_from=now)
    credits.record_recharge(
        owner.account_space_id,
        package.version_id,
        payment_reference="payment-delete",
        occurred_at=now,
    )
    tasks = InMemoryGenerationTasks(credits, canvases=InMemoryCanvases(), max_active_tasks=2)
    tasks.submit(
        GenerationSubmission(
            user_id=owner.user_id,
            account_space_id=owner.account_space_id,
            canvas_id=None,
            task_id="task-delete",
            logical_model="gpt-image-2",
            output_spec="4k",
            quantity=1,
            prompt="delete this temporary result",
            params=GenerationParameters(aspect_ratio="1:1"),
            submitted_at=now,
        )
    )
    tasks.transition(
        owner.account_space_id,
        "task-delete",
        GenerationStarted(provider_task_id="provider-delete", occurred_at=now),
    )
    objects = InMemoryMediaObjects({"temporary/delete/result.png"})
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=objects,
        storage_allowances=InMemoryStorageAllowances({owner.account_space_id: 20 * 1024 * 1024}),
        id_factory=lambda: "media-delete",
    )
    media.register(
        GeneratedMediaRegistration(
            user_id=owner.user_id,
            account_space_id=owner.account_space_id,
            canvas_id=None,
            task_id="task-delete",
            result_reference="result-delete",
            object_key="temporary/delete/result.png",
            kind="image",
            mime_type="image/png",
            size_bytes=2 * 1024 * 1024,
            content_hash="d" * 64,
            created_at=now,
        )
    )
    client = TestClient(create_app(accounts, generated_media=media, clock=lambda: now))
    headers = {"Authorization": f"Bearer {session.access_token}"}

    before = client.get("/api/v1/auth/me", headers=headers)
    deleted = client.delete("/api/v1/media/media-delete", headers=headers)
    after = client.get("/api/v1/auth/me", headers=headers)

    assert before.json()["storage_allowance"]["used_bytes"] == 2 * 1024 * 1024
    assert deleted.status_code == 204
    assert after.json()["storage_allowance"]["used_bytes"] == 0
    assert media.get(owner.account_space_id, "media-delete").state.value == "deleted"
