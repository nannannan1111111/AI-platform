from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.assets import InMemoryPersonalAssets
from app.canvases import CanvasCreation, InMemoryCanvases
from app.credits import InMemoryCredits
from app.generation import GenerationParameters, GenerationStarted, GenerationSubmission, InMemoryGenerationTasks
from app.http import create_app
from app.media import (
    GeneratedMediaRegistration,
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


def test_authenticated_owner_saves_renames_and_lists_an_account_scoped_personal_asset() -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    owner = accounts.register("owner@example.com", "a-correct-horse-battery-staple")
    other = accounts.register("other@example.com", "a-correct-horse-battery-staple")
    owner_session = accounts.login("owner@example.com", "a-correct-horse-battery-staple")
    other_session = accounts.login("other@example.com", "a-correct-horse-battery-staple")
    owner_headers = {"Authorization": f"Bearer {owner_session.access_token}"}
    other_headers = {"Authorization": f"Bearer {other_session.access_token}"}
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
            title="资产来源画布",
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
    temporary_key = "temporary/owner/task-1/result.png"
    generated_media = InMemoryGeneratedMedia(
        tasks,
        media_objects=_FailOnceOnDeleteMediaObjects({temporary_key}),
        storage_allowances=InMemoryStorageAllowances({owner.account_space_id: 100}),
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
    personal_assets = InMemoryPersonalAssets(generated_media, id_factory=lambda: "asset-1")
    client = TestClient(
        create_app(
            accounts,
            generated_media=generated_media,
            personal_assets=personal_assets,
            clock=lambda: now,
        )
    )

    response = client.post(
        "/api/v1/personal-assets",
        headers=owner_headers,
        json={
            "media_id": "media-1",
            "display_name": "角色立绘",
            "idempotency_key": "save-result-1",
            "user_id": other.user_id,
            "account_space_id": other.account_space_id,
        },
    )
    replay = client.post(
        "/api/v1/personal-assets",
        headers=owner_headers,
        json={
            "media_id": "media-1",
            "display_name": "角色立绘",
            "idempotency_key": "save-result-1",
        },
    )

    assert response.status_code == 201
    asset = response.json()
    assert asset["asset_id"] == "asset-1"
    assert asset["user_id"] == owner.user_id
    assert asset["account_space_id"] == owner.account_space_id
    assert replay.json() == asset
    rename = client.patch(
        "/api/v1/personal-assets/asset-1",
        headers=owner_headers,
        json={"display_name": "  主角立绘  "},
    )
    assert rename.status_code == 200
    renamed = rename.json()
    assert renamed["display_name"] == "主角立绘"
    assert renamed["media_id"] == "media-1"
    assert (
        client.patch(
            "/api/v1/personal-assets/asset-1",
            headers=other_headers,
            json={"display_name": "越权重命名"},
        ).status_code
        == 404
    )
    assert (
        client.patch(
            "/api/v1/personal-assets/asset-1",
            headers=owner_headers,
            json={"display_name": "   "},
        ).status_code
        == 422
    )
    assert (
        client.patch(
            "/api/v1/personal-assets/asset-1",
            headers=owner_headers,
            json={"display_name": "  主角立绘  "},
        ).json()
        == renamed
    )
    assert client.get("/api/v1/personal-assets", headers=owner_headers).json() == [renamed]
    assert client.get("/api/v1/personal-assets", headers=other_headers).json() == []
    assert (
        client.post(
            "/api/v1/personal-assets",
            headers=other_headers,
            json={
                "media_id": "media-1",
                "display_name": "越权资产",
                "idempotency_key": "other-save",
            },
        ).status_code
        == 404
    )

    assert client.delete("/api/v1/personal-assets/asset-1", headers=other_headers).status_code == 404

    removal = client.delete("/api/v1/personal-assets/asset-1", headers=owner_headers)

    assert removal.status_code == 503
    assert client.get("/api/v1/personal-assets", headers=owner_headers).json() == []
    assert client.delete("/api/v1/personal-assets/asset-1", headers=owner_headers).status_code == 204
    assert client.get("/api/v1/personal-assets", headers=owner_headers).json() == []
    assert (
        client.patch(
            "/api/v1/personal-assets/asset-1",
            headers=owner_headers,
            json={"display_name": "不能恢复"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/personal-assets",
            headers=owner_headers,
            json={
                "media_id": "media-1",
                "display_name": "不能复活",
                "idempotency_key": "save-result-1",
            },
        ).status_code
        == 409
    )
