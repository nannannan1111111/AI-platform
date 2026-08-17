from pathlib import Path

from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.http import create_app
from app.media import FileSystemMediaObjects
from app.platform_content import (
    InMemoryPlatformContentSettings,
    PlatformContentUpdate,
    SqlAlchemyPlatformContentSettings,
)


def _registered_client(settings: InMemoryPlatformContentSettings) -> tuple[TestClient, dict[str, str], list[str]]:
    accounts = InMemoryAccountAccess()
    accounts.register("artist@example.com", "a-correct-horse-battery-staple")
    token = accounts.login("artist@example.com", "a-correct-horse-battery-staple").access_token
    authorized: list[str] = []
    client = TestClient(create_app(accounts, platform_content=settings, admin_authorizer=authorized.append))
    return client, {"Authorization": f"Bearer {token}"}, authorized


def test_platform_content_api_publishes_text_and_images_and_requires_admin_for_updates() -> None:
    settings = InMemoryPlatformContentSettings()
    client, headers, authorized = _registered_client(settings)

    response = client.put(
        "/api/v1/admin/platform-content",
        headers=headers,
        data={"announcement_text": "系统维护通知", "support_text": "客服时间 9:00-18:00"},
        files={"announcement_image": ("notice.png", b"png-image", "image/png")},
    )

    assert response.status_code == 200
    assert authorized
    public = client.get("/api/v1/platform-content", headers=headers)
    assert public.status_code == 200
    assert public.json()["announcement_text"] == "系统维护通知"
    assert public.json()["announcement_image_url"].endswith("/announcement/image")
    assert public.json()["support_text"] == "客服时间 9:00-18:00"
    assert client.get(public.json()["announcement_image_url"], headers=headers).content == b"png-image"
    assert client.get("/api/v1/platform-content").status_code == 401


def test_sql_platform_content_persists_text_and_replaces_images(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'platform-content.db').as_posix()}"
    media = FileSystemMediaObjects(tmp_path / "media")
    (tmp_path / "media").mkdir()
    settings = SqlAlchemyPlatformContentSettings.for_database_url(database_url, media, initialize_schema=True)

    settings.update(PlatformContentUpdate("公告一", "客服一", b"one", "image/png"))
    settings.update(PlatformContentUpdate("公告二", "客服二", b"two", "image/webp"))

    restarted = SqlAlchemyPlatformContentSettings.for_database_url(database_url, media)
    assert restarted.current().announcement_text == "公告二"
    assert restarted.image("announcement") == (b"two", "image/webp")
