import base64
from datetime import UTC, datetime, timedelta

import pytest

from app.reference_media import (
    InMemoryReferenceMedia,
    ReferenceMediaExpired,
    ReferenceMediaNotFound,
    ReferenceMediaOrigin,
    ReferenceMediaUpload,
)

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_reference_image_is_account_isolated_and_expires_after_24_hours() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    media = InMemoryReferenceMedia(id_factory=lambda: "reference-1")

    uploaded = media.upload(
        ReferenceMediaUpload(
            user_id="user-1",
            account_space_id="account-space-1",
            original_name="portrait.png",
            declared_mime_type="image/png",
            content=_PNG_BYTES,
            created_at=now,
        )
    )

    assert uploaded.media_id == "reference-1"
    assert uploaded.mime_type == "image/png"
    assert uploaded.size_bytes == len(_PNG_BYTES)
    assert uploaded.expires_at == now + timedelta(hours=24)
    assert media.read("account-space-1", "reference-1", at=now + timedelta(hours=23)).content == _PNG_BYTES
    with pytest.raises(ReferenceMediaNotFound):
        media.read("another-account-space", "reference-1", at=now)
    with pytest.raises(ReferenceMediaExpired):
        media.read("account-space-1", "reference-1", at=now + timedelta(hours=24))


def test_reference_image_can_be_listed_and_permanently_deleted_by_its_owner() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    media = InMemoryReferenceMedia(id_factory=lambda: "reference-delete")
    uploaded = media.upload(
        ReferenceMediaUpload(
            user_id="user-1",
            account_space_id="account-space-1",
            original_name="deletable.png",
            declared_mime_type="image/png",
            content=_PNG_BYTES,
            created_at=now,
        )
    )

    assert media.list_recent("account-space-1", at=now) == (uploaded,)
    assert media.list_recent("other-account", at=now) == ()

    deleted = media.delete("account-space-1", "reference-delete")

    assert deleted.state.value == "deleted"
    assert media.list_recent("account-space-1", at=now) == ()
    with pytest.raises(ReferenceMediaNotFound):
        media.read("account-space-1", "reference-delete", at=now)


def test_canvas_reference_remains_readable_but_is_hidden_from_standalone_recents() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    media = InMemoryReferenceMedia(id_factory=lambda: "canvas-reference")
    uploaded = media.upload(
        ReferenceMediaUpload(
            user_id="user-1",
            account_space_id="account-space-1",
            original_name="canvas-source.png",
            declared_mime_type="image/png",
            content=_PNG_BYTES,
            created_at=now,
            origin=ReferenceMediaOrigin.CANVAS,
        )
    )

    assert media.read("account-space-1", uploaded.media_id, at=now).content == _PNG_BYTES
    assert media.list_recent("account-space-1", at=now) == ()
