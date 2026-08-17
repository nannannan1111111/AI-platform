import base64
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.canvases import InMemoryCanvases
from app.credits import InMemoryCredits
from app.generation import InMemoryGenerationTasks
from app.http import create_app
from app.media import FileSystemMediaObjects, InMemoryGeneratedMedia, InMemoryStorageAllowances

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class _FailingMediaReconciliation:
    def reconcile_canvas_references(self, *args: object) -> None:
        raise RuntimeError("媒体协调暂不可用")


class _ActiveCanvasGenerationTasks:
    def __init__(self, account_space_id: str, canvas_id: str) -> None:
        self._account_space_id = account_space_id
        self._canvas_id = canvas_id

    def active_for_canvas(self, account_space_id: str, canvas_id: str) -> tuple[object, ...]:
        if account_space_id == self._account_space_id and canvas_id == self._canvas_id:
            return (object(),)
        return ()


class _RecordingMediaReconciliation:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[str, ...], datetime]] = []

    def reconcile_canvas_references(
        self,
        account_space_id: str,
        canvas_id: str,
        retained_media_ids: tuple[str, ...],
        occurred_at: datetime,
    ) -> None:
        self.calls.append((account_space_id, canvas_id, retained_media_ids, occurred_at))


def test_authenticated_user_creates_and_reads_only_their_canvas() -> None:
    now = datetime(2026, 8, 8, 15, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    first = accounts.register("first@example.com", "a-correct-horse-battery-staple")
    second = accounts.register("second@example.com", "a-correct-horse-battery-staple")
    first_session = accounts.login("first@example.com", "a-correct-horse-battery-staple")
    second_session = accounts.login("second@example.com", "a-correct-horse-battery-staple")
    canvases = InMemoryCanvases()
    client = TestClient(create_app(accounts, canvases=canvases, clock=lambda: now))

    response = client.post(
        "/api/v1/canvases",
        headers={"Authorization": f"Bearer {first_session.access_token}"},
        json={
            "title": "第一张画布",
            "kind": "classic",
            "user_id": second.user_id,
            "account_space_id": second.account_space_id,
        },
    )

    assert response.status_code == 201
    canvas = response.json()
    assert canvas["user_id"] == first.user_id
    assert canvas["account_space_id"] == first.account_space_id
    assert canvas["title"] == "第一张画布"
    assert canvas["kind"] == "classic"
    assert canvas["version"] == 1
    assert canvas["document"] == {
        "nodes": [],
        "connections": [],
        "viewport": {"x": 0, "y": 0, "scale": 1},
    }
    assert (
        client.get(
            f"/api/v1/canvases/{canvas['canvas_id']}",
            headers={"Authorization": f"Bearer {first_session.access_token}"},
        ).json()
        == canvas
    )
    assert (
        client.get(
            f"/api/v1/canvases/{canvas['canvas_id']}",
            headers={"Authorization": f"Bearer {second_session.access_token}"},
        ).status_code
        == 404
    )


def test_canvas_owner_uploads_an_account_isolated_persistent_image(tmp_path: Path) -> None:
    now = datetime(2026, 8, 8, 15, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    owner = accounts.register("canvas-upload@example.com", "a-correct-horse-battery-staple")
    accounts.register("other-upload@example.com", "a-correct-horse-battery-staple")
    owner_session = accounts.login("canvas-upload@example.com", "a-correct-horse-battery-staple")
    other_session = accounts.login("other-upload@example.com", "a-correct-horse-battery-staple")
    canvases = InMemoryCanvases(id_factory=lambda: "canvas-upload")
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={owner.account_space_id})
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    objects = FileSystemMediaObjects(tmp_path / "canvas-media")
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=objects,
        storage_allowances=InMemoryStorageAllowances({owner.account_space_id: 20 * 1024 * 1024}),
        id_factory=lambda: "canvas-media-1",
    )
    client = TestClient(
        create_app(
            accounts,
            canvases=canvases,
            generated_media=media,
            media_content=objects,
            clock=lambda: now,
        )
    )
    owner_headers = {"Authorization": f"Bearer {owner_session.access_token}"}
    other_headers = {"Authorization": f"Bearer {other_session.access_token}"}
    client.post("/api/v1/canvases", headers=owner_headers, json={"title": "Upload", "kind": "classic"})

    response = client.post(
        "/api/v1/canvases/canvas-upload/media",
        headers=owner_headers,
        files=[("files", ("portrait.png", _PNG_BYTES, "image/png"))],
    )

    assert response.status_code == 201
    assert response.json() == {
        "files": [
            {
                "media_id": "canvas-media-1",
                "name": "portrait.png",
                "kind": "image",
                "mime_type": "image/png",
                "url": "/api/v1/media/canvas-media-1/content",
            }
        ]
    }
    assert client.get("/api/v1/media/canvas-media-1/content", headers=owner_headers).content == _PNG_BYTES
    assert client.get("/api/v1/media/canvas-media-1/content", headers=other_headers).status_code == 404
    assert media.storage_allowance(owner.account_space_id).used_bytes == len(_PNG_BYTES)
    assert (
        client.post(
            "/api/v1/canvases/canvas-upload/media",
            headers=other_headers,
            files=[("files", ("hidden.png", _PNG_BYTES, "image/png"))],
        ).status_code
        == 404
    )


def test_smart_canvas_imports_a_local_workflow_package_into_quota_counted_storage(tmp_path: Path) -> None:
    now = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    owner = accounts.register("workflow-owner@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("workflow-owner@example.com", "a-correct-horse-battery-staple")
    canvases = InMemoryCanvases(id_factory=lambda: "smart-workflow-canvas")
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={owner.account_space_id})
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    objects = FileSystemMediaObjects(tmp_path / "workflow-media")
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=objects,
        storage_allowances=InMemoryStorageAllowances({owner.account_space_id: 20 * 1024 * 1024}),
        id_factory=lambda: "imported-media-1",
    )
    client = TestClient(
        create_app(
            accounts,
            canvases=canvases,
            generated_media=media,
            media_content=objects,
            clock=lambda: now,
        )
    )
    headers = {"Authorization": f"Bearer {session.access_token}"}
    client.post(
        "/api/v1/canvases",
        headers=headers,
        json={"title": "Portable", "kind": "smart"},
    )
    workflow = {
        "format": "infinite-smart-canvas-workflow",
        "version": 1,
        "nodes": [{"id": "node-1", "type": "smart-image", "images": [{"media_id": "portable-1"}]}],
        "connections": [],
    }
    manifest = {
        "version": 1,
        "resources": [
            {
                "media_id": "portable-1",
                "path": "resources/portable-1.png",
                "name": "portable.png",
                "mime_type": "image/png",
            }
        ],
    }
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("workflow.json", json.dumps(workflow))
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("resources/portable-1.png", _PNG_BYTES)

    imported = client.post(
        "/api/v1/canvases/smart-workflow-canvas/workflows/import",
        headers=headers,
        files={"file": ("workflow.zip", package.getvalue(), "application/zip")},
    )

    assert imported.status_code == 200
    imported_image = imported.json()["nodes"][0]["images"][0]
    assert imported_image["media_id"] == "imported-media-1"
    assert imported_image["url"] == "/api/v1/media/imported-media-1/content"
    assert media.storage_allowance(owner.account_space_id).used_bytes == len(_PNG_BYTES)
    assert client.get(imported_image["url"], headers=headers).content == _PNG_BYTES

    exported = client.post(
        "/api/v1/canvases/smart-workflow-canvas/workflows/export",
        headers=headers,
        json=imported.json(),
    )

    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert {"workflow.json", "manifest.json", "resources/imported-media-1.png"} <= set(archive.namelist())
        assert archive.read("resources/imported-media-1.png") == _PNG_BYTES


def test_smart_workflow_import_is_blocked_before_upload_when_storage_is_insufficient(tmp_path: Path) -> None:
    now = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    owner = accounts.register("workflow-quota@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("workflow-quota@example.com", "a-correct-horse-battery-staple")
    canvases = InMemoryCanvases(id_factory=lambda: "quota-canvas")
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={owner.account_space_id})
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    objects = FileSystemMediaObjects(tmp_path / "quota-media")
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=objects,
        storage_allowances=InMemoryStorageAllowances({owner.account_space_id: len(_PNG_BYTES) - 1}),
        id_factory=lambda: "must-not-upload",
    )
    client = TestClient(create_app(accounts, canvases=canvases, generated_media=media, media_content=objects, clock=lambda: now))
    headers = {"Authorization": f"Bearer {session.access_token}"}
    client.post("/api/v1/canvases", headers=headers, json={"title": "Quota", "kind": "smart"})
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("workflow.json", json.dumps({"nodes": [{"id": "n", "images": [{"media_id": "old"}]}]}))
        archive.writestr("manifest.json", json.dumps({"resources": [{"media_id": "old", "path": "resources/a.png", "name": "a.png", "mime_type": "image/png"}]}))
        archive.writestr("resources/a.png", _PNG_BYTES)

    response = client.post(
        "/api/v1/canvases/quota-canvas/workflows/import",
        headers=headers,
        files={"file": ("workflow.zip", package.getvalue(), "application/zip")},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "个人存储空间不足，无法导入工作流"
    assert media.storage_allowance(owner.account_space_id).used_bytes == 0


def test_smart_workflow_import_allows_more_than_twenty_images(tmp_path: Path) -> None:
    now = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    owner = accounts.register("workflow-many@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("workflow-many@example.com", "a-correct-horse-battery-staple")
    canvases = InMemoryCanvases(id_factory=lambda: "many-images-canvas")
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={owner.account_space_id})
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    objects = FileSystemMediaObjects(tmp_path / "many-images-media")
    generated_ids = iter(f"imported-{index}" for index in range(21))
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=objects,
        storage_allowances=InMemoryStorageAllowances({owner.account_space_id: 1024 * 1024}),
        id_factory=lambda: next(generated_ids),
    )
    client = TestClient(
        create_app(accounts, canvases=canvases, generated_media=media, media_content=objects, clock=lambda: now)
    )
    headers = {"Authorization": f"Bearer {session.access_token}"}
    client.post("/api/v1/canvases", headers=headers, json={"title": "Many", "kind": "smart"})
    resource_ids = [f"portable-{index}" for index in range(21)]
    workflow = {
        "nodes": [{"id": "node-many", "images": [{"media_id": media_id} for media_id in resource_ids]}],
        "connections": [],
    }
    manifest = {
        "resources": [
            {
                "media_id": media_id,
                "path": f"resources/{media_id}.png",
                "name": f"{media_id}.png",
                "mime_type": "image/png",
            }
            for media_id in resource_ids
        ]
    }
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("workflow.json", json.dumps(workflow))
        archive.writestr("manifest.json", json.dumps(manifest))
        for media_id in resource_ids:
            archive.writestr(f"resources/{media_id}.png", _PNG_BYTES)

    response = client.post(
        "/api/v1/canvases/many-images-canvas/workflows/import",
        headers=headers,
        files={"file": ("workflow.zip", package.getvalue(), "application/zip")},
    )

    assert response.status_code == 200
    assert len(response.json()["nodes"][0]["images"]) == 21
    assert media.storage_allowance(owner.account_space_id).used_bytes == len(_PNG_BYTES)


def test_smart_workflow_import_rejects_unsafe_zip_paths_and_abnormal_compression(tmp_path: Path) -> None:
    now = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    owner = accounts.register("workflow-safety@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("workflow-safety@example.com", "a-correct-horse-battery-staple")
    canvases = InMemoryCanvases(id_factory=lambda: "safe-import-canvas")
    credits = InMemoryCredits(clock=lambda: now, account_space_ids={owner.account_space_id})
    tasks = InMemoryGenerationTasks(credits, canvases=canvases, max_active_tasks=2)
    objects = FileSystemMediaObjects(tmp_path / "safe-import-media")
    media = InMemoryGeneratedMedia(
        tasks,
        media_objects=objects,
        storage_allowances=InMemoryStorageAllowances({owner.account_space_id: 20 * 1024 * 1024}),
    )
    client = TestClient(
        create_app(accounts, canvases=canvases, generated_media=media, media_content=objects, clock=lambda: now)
    )
    headers = {"Authorization": f"Bearer {session.access_token}"}
    client.post("/api/v1/canvases", headers=headers, json={"title": "Safe", "kind": "smart"})
    workflow = {"nodes": [{"id": "node-safe"}], "connections": []}
    manifest = {"resources": []}

    unsafe_path_package = io.BytesIO()
    with zipfile.ZipFile(unsafe_path_package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("workflow.json", json.dumps(workflow))
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("../escape.txt", "unsafe")
    unsafe_path_response = client.post(
        "/api/v1/canvases/safe-import-canvas/workflows/import",
        headers=headers,
        files={"file": ("unsafe.zip", unsafe_path_package.getvalue(), "application/zip")},
    )

    compression_bomb_package = io.BytesIO()
    with zipfile.ZipFile(compression_bomb_package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("workflow.json", json.dumps(workflow))
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("resources/compressed.bin", b"0" * (2 * 1024 * 1024))
    compression_response = client.post(
        "/api/v1/canvases/safe-import-canvas/workflows/import",
        headers=headers,
        files={"file": ("compressed.zip", compression_bomb_package.getvalue(), "application/zip")},
    )

    assert unsafe_path_response.status_code == 422
    assert unsafe_path_response.json()["detail"] == "工作流 ZIP 包含不安全路径"
    assert compression_response.status_code == 422
    assert compression_response.json()["detail"] == "工作流 ZIP 压缩比异常"
    assert media.storage_allowance(owner.account_space_id).used_bytes == 0


def test_canvas_list_contains_only_the_current_account_space() -> None:
    now = datetime(2026, 8, 8, 15, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    accounts.register("first@example.com", "a-correct-horse-battery-staple")
    accounts.register("second@example.com", "a-correct-horse-battery-staple")
    first_session = accounts.login("first@example.com", "a-correct-horse-battery-staple")
    second_session = accounts.login("second@example.com", "a-correct-horse-battery-staple")
    canvases = InMemoryCanvases()
    client = TestClient(create_app(accounts, canvases=canvases, clock=lambda: now))
    first_headers = {"Authorization": f"Bearer {first_session.access_token}"}
    second_headers = {"Authorization": f"Bearer {second_session.access_token}"}
    first_canvas = client.post(
        "/api/v1/canvases",
        headers=first_headers,
        json={"title": "第一张画布", "kind": "classic"},
    ).json()
    client.post(
        "/api/v1/canvases",
        headers=second_headers,
        json={"title": "另一账户的画布", "kind": "smart"},
    )

    response = client.get("/api/v1/canvases", headers=first_headers)

    assert response.status_code == 200
    assert response.json() == [first_canvas]


def test_authenticated_user_permanently_deletes_their_canvas() -> None:
    now = datetime(2026, 8, 8, 15, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    headers = {"Authorization": f"Bearer {session.access_token}"}
    client = TestClient(create_app(accounts, canvases=InMemoryCanvases(), clock=lambda: now))
    canvas = client.post(
        "/api/v1/canvases",
        headers=headers,
        json={"title": "待删除画布", "kind": "classic"},
    ).json()

    response = client.delete(f"/api/v1/canvases/{canvas['canvas_id']}", headers=headers)

    assert response.status_code == 204
    assert client.get(f"/api/v1/canvases/{canvas['canvas_id']}", headers=headers).status_code == 404
    assert client.get("/api/v1/canvases", headers=headers).json() == []
    assert client.delete(f"/api/v1/canvases/{canvas['canvas_id']}", headers=headers).status_code == 404


def test_canvas_deletion_does_not_reveal_or_remove_another_accounts_canvas() -> None:
    now = datetime(2026, 8, 9, 9, 30, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    accounts.register("owner@example.com", "a-correct-horse-battery-staple")
    accounts.register("other@example.com", "a-correct-horse-battery-staple")
    owner_session = accounts.login("owner@example.com", "a-correct-horse-battery-staple")
    other_session = accounts.login("other@example.com", "a-correct-horse-battery-staple")
    owner_headers = {"Authorization": f"Bearer {owner_session.access_token}"}
    other_headers = {"Authorization": f"Bearer {other_session.access_token}"}
    client = TestClient(create_app(accounts, canvases=InMemoryCanvases(), clock=lambda: now))
    canvas = client.post(
        "/api/v1/canvases",
        headers=owner_headers,
        json={"title": "仅所有者可删除", "kind": "classic"},
    ).json()

    response = client.delete(f"/api/v1/canvases/{canvas['canvas_id']}", headers=other_headers)

    assert response.status_code == 404
    assert client.get(f"/api/v1/canvases/{canvas['canvas_id']}", headers=owner_headers).status_code == 200


def test_canvas_with_active_generation_requires_explicit_deletion_confirmation() -> None:
    now = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("running@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("running@example.com", "a-correct-horse-battery-staple")
    headers = {"Authorization": f"Bearer {session.access_token}"}
    canvases = InMemoryCanvases(id_factory=lambda: "canvas-running-1")
    generation_tasks = _ActiveCanvasGenerationTasks(registration.account_space_id, "canvas-running-1")
    client = TestClient(
        create_app(
            accounts,
            canvases=canvases,
            generation_tasks=generation_tasks,  # type: ignore[arg-type]
            clock=lambda: now,
        )
    )
    client.post(
        "/api/v1/canvases",
        headers=headers,
        json={"title": "仍在生成", "kind": "smart"},
    )

    blocked = client.delete("/api/v1/canvases/canvas-running-1", headers=headers)

    assert blocked.status_code == 409
    assert blocked.json() == {
        "detail": {
            "confirm_required": True,
            "message": "画布仍有生成任务运行；永久删除后任务继续执行，但结果不再回到该画布。是否继续？",
        }
    }
    assert client.get("/api/v1/canvases/canvas-running-1", headers=headers).status_code == 200
    confirmed = client.delete(
        "/api/v1/canvases/canvas-running-1?confirm_running_tasks=true",
        headers=headers,
    )
    assert confirmed.status_code == 204
    assert client.get("/api/v1/canvases/canvas-running-1", headers=headers).status_code == 404


def test_canvas_deletion_releases_all_of_its_media_references() -> None:
    now = datetime(2026, 8, 9, 10, 30, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    registration = accounts.register("media@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("media@example.com", "a-correct-horse-battery-staple")
    headers = {"Authorization": f"Bearer {session.access_token}"}
    media = _RecordingMediaReconciliation()
    client = TestClient(
        create_app(
            accounts,
            canvases=InMemoryCanvases(id_factory=lambda: "canvas-media-1"),
            generated_media=media,  # type: ignore[arg-type]
            clock=lambda: now,
        )
    )
    client.post(
        "/api/v1/canvases",
        headers=headers,
        json={"title": "释放媒体引用", "kind": "classic"},
    )

    response = client.delete("/api/v1/canvases/canvas-media-1", headers=headers)

    assert response.status_code == 204
    assert media.calls == [(registration.account_space_id, "canvas-media-1", (), now)]


def test_canvas_deletion_remains_irreversible_when_media_reconciliation_is_unavailable() -> None:
    now = datetime(2026, 8, 9, 10, 45, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    accounts.register("media-failure@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("media-failure@example.com", "a-correct-horse-battery-staple")
    headers = {"Authorization": f"Bearer {session.access_token}"}
    client = TestClient(
        create_app(
            accounts,
            canvases=InMemoryCanvases(id_factory=lambda: "canvas-media-failure"),
            generated_media=_FailingMediaReconciliation(),  # type: ignore[arg-type]
            clock=lambda: now,
        ),
        raise_server_exceptions=False,
    )
    client.post(
        "/api/v1/canvases",
        headers=headers,
        json={"title": "媒体协调失败", "kind": "classic"},
    )

    response = client.delete("/api/v1/canvases/canvas-media-failure", headers=headers)

    assert response.status_code == 204
    assert client.get("/api/v1/canvases/canvas-media-failure", headers=headers).status_code == 404


def test_canvas_save_uses_an_expected_version_and_preserves_unknown_nodes() -> None:
    now = datetime(2026, 8, 8, 15, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    canvases = InMemoryCanvases()
    client = TestClient(create_app(accounts, canvases=canvases, clock=lambda: now))
    headers = {"Authorization": f"Bearer {session.access_token}"}
    canvas = client.post(
        "/api/v1/canvases",
        headers=headers,
        json={"title": "待编辑画布", "kind": "smart"},
    ).json()
    document = {
        "nodes": [{"id": "node-1", "type": "future-node", "custom": {"keep": True}}],
        "connections": [],
        "viewport": {"x": 12, "y": 34, "scale": 0.8},
    }

    response = client.put(
        f"/api/v1/canvases/{canvas['canvas_id']}",
        headers=headers,
        json={"expected_version": 1, "title": "已保存画布", "document": document},
    )

    assert response.status_code == 200
    saved = response.json()
    assert saved["version"] == 2
    assert saved["title"] == "已保存画布"
    assert saved["document"] == document
    assert (
        client.put(
            f"/api/v1/canvases/{canvas['canvas_id']}",
            headers=headers,
            json={"expected_version": 1, "title": "过期覆盖", "document": {"nodes": []}},
        ).status_code
        == 409
    )
    assert client.get(f"/api/v1/canvases/{canvas['canvas_id']}", headers=headers).json() == saved


def test_canvas_save_succeeds_when_media_reference_reconciliation_is_unavailable() -> None:
    now = datetime(2026, 8, 8, 15, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    session = accounts.login("artist@example.com", "a-correct-horse-battery-staple")
    headers = {"Authorization": f"Bearer {session.access_token}"}
    canvases = InMemoryCanvases(id_factory=lambda: "canvas-1")
    client = TestClient(
        create_app(
            accounts,
            canvases=canvases,
            generated_media=_FailingMediaReconciliation(),  # type: ignore[arg-type]
            clock=lambda: now,
        ),
        raise_server_exceptions=False,
    )
    client.post(
        "/api/v1/canvases",
        headers=headers,
        json={"title": "协调失败画布", "kind": "classic"},
    )

    response = client.put(
        "/api/v1/canvases/canvas-1",
        headers=headers,
        json={
            "expected_version": 1,
            "document": {
                "nodes": [],
                "connections": [],
                "viewport": {"x": 0, "y": 0, "scale": 1},
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["version"] == 2
