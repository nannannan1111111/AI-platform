import base64
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.http import create_app
from app.reference_media import InMemoryReferenceMedia

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_reference_image_upload_and_preview_are_account_isolated() -> None:
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    accounts.register("owner@example.com", "a-correct-horse-battery-staple")
    accounts.register("other@example.com", "a-correct-horse-battery-staple")
    owner = accounts.login("owner@example.com", "a-correct-horse-battery-staple")
    other = accounts.login("other@example.com", "a-correct-horse-battery-staple")
    client = TestClient(
        create_app(
            accounts,
            reference_media=InMemoryReferenceMedia(id_factory=lambda: "reference-1"),
            clock=lambda: now,
        )
    )

    response = client.post(
        "/api/v1/reference-media",
        headers={"Authorization": f"Bearer {owner.access_token}"},
        files={"file": ("portrait.png", _PNG_BYTES, "image/png")},
    )

    assert response.status_code == 201
    assert response.json() == {
        "media_id": "reference-1",
        "original_name": "portrait.png",
        "mime_type": "image/png",
        "size_bytes": len(_PNG_BYTES),
        "expires_at": "2026-08-11T12:00:00Z",
        "preview_url": "/api/v1/reference-media/reference-1/content",
    }
    preview = client.get(
        response.json()["preview_url"],
        headers={"Authorization": f"Bearer {owner.access_token}"},
    )
    hidden = client.get(
        response.json()["preview_url"],
        headers={"Authorization": f"Bearer {other.access_token}"},
    )
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert preview.content == _PNG_BYTES
    assert hidden.status_code == 404
    assert "object_key" not in response.text
    assert "account_space_id" not in response.text

    recent = client.get(
        "/api/v1/reference-media/recent",
        headers={"Authorization": f"Bearer {owner.access_token}"},
    )
    hidden_recent = client.get(
        "/api/v1/reference-media/recent",
        headers={"Authorization": f"Bearer {other.access_token}"},
    )
    deleted = client.delete(
        "/api/v1/reference-media/reference-1",
        headers={"Authorization": f"Bearer {owner.access_token}"},
    )

    assert recent.status_code == 200
    assert recent.json() == [response.json()]
    assert hidden_recent.json() == []
    assert deleted.status_code == 204
    assert (
        client.get(
            response.json()["preview_url"], headers={"Authorization": f"Bearer {owner.access_token}"}
        ).status_code
        == 404
    )
    assert (
        client.get("/api/v1/reference-media/recent", headers={"Authorization": f"Bearer {owner.access_token}"}).json()
        == []
    )


def test_reference_image_raw_upload_avoids_multipart_parsing_and_preserves_unicode_name() -> None:
    now = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    accounts = InMemoryAccountAccess(clock=lambda: now)
    accounts.register("owner@example.com", "a-correct-horse-battery-staple")
    owner = accounts.login("owner@example.com", "a-correct-horse-battery-staple")
    client = TestClient(
        create_app(
            accounts,
            reference_media=InMemoryReferenceMedia(id_factory=lambda: "raw-reference-1"),
            clock=lambda: now,
        )
    )

    response = client.post(
        "/api/v1/reference-media/content",
        headers={
            "Authorization": f"Bearer {owner.access_token}",
            "Content-Type": "image/png",
            "X-Reference-Filename": quote("中文参考图.png"),
        },
        content=_PNG_BYTES,
    )

    assert response.status_code == 201
    assert response.json() == {
        "media_id": "raw-reference-1",
        "original_name": "中文参考图.png",
        "mime_type": "image/png",
        "size_bytes": len(_PNG_BYTES),
        "expires_at": "2026-08-16T12:00:00Z",
        "preview_url": "/api/v1/reference-media/raw-reference-1/content",
    }
